"""ingest/pdf.py — PDF 政府文档源适配器（M3）（DEVLOG M3-D2）

流程：源清单（configs/pdf_sources.json 或显式 paths）→ 取字节（本地/URL，验 %PDF 魔数）
→ **原始字节按 sha256 归档**（raw 层不可变，raw_uri 指向归档件）→ parse.pdf_extractor
抽文本 → spine 行（source_id = sha256(字节)，registry 声明）。
提取失败/疑似扫描件 → quarantine（文件级），聚合护栏同 oscar 的 run 级语义。
"""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

from common import ids
from common.first_seen import FirstSeenLedger
from common.idempotent import run_stage
from common.textstats import text_stats
from parse.pdf_extractor import extract_pdf
from ingest.spine_schema import SPINE_PARQUET_SCHEMA

ROOT = Path(__file__).resolve().parent.parent
SOURCE = "pdf"
PIPELINE_VERSION = "ingest-pdf-0.1.0"
SOURCES_CONFIG = ROOT / "configs" / "pdf_sources.json"
OUTPUT_DIR = ROOT / "data" / "raw" / "pdf"
LEDGER_PATH = ROOT / "data" / "meta" / "first_seen" / f"{SOURCE}.json"
RAW_ARCHIVE = ROOT / "data" / "raw" / "pdf" / "files"

QUARANTINE_SCHEMA = pa.schema(
    [
        pa.field("name", pa.string(), nullable=False),
        pa.field("reason", pa.string(), nullable=False),
        pa.field("sha256", pa.string(), nullable=False),
        pa.field("warnings", pa.list_(pa.string())),
    ]
)


@lru_cache(maxsize=1)
def _registry():
    reg = json.loads((ROOT / "configs" / "source_registry.json").read_text(encoding="utf-8"))
    return reg["sources"][SOURCE]


@lru_cache(maxsize=1)
def _policy():
    return json.loads((ROOT / "configs" / "ingest_policy.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _schema_version():
    return json.loads((ROOT / "schema" / "unified_schema.json").read_text(encoding="utf-8"))["version"]


def load_entries(config_path: Path | None = None) -> list:
    """读源清单：[{"name","url"} | {"name","path"}]。清单为空返回 []（调用方决定是否报错）。

    异常: json.JSONDecodeError — 清单坏（fail-fast，人工文件不允许静默）
    """
    path = Path(config_path) if config_path else SOURCES_CONFIG
    if not path.exists():
        return []
    entries = json.loads(path.read_text(encoding="utf-8"))["files"]
    return entries


def fetch_bytes(entry: dict) -> bytes:
    """取一个源的字节并验 %PDF 魔数。

    参数: entry — {"name", "url"} 或 {"name", "path"}
    返回: 原始字节
    异常: ValueError — 魔数不是 %PDF（URL 返回了 HTML 错误页之类）；requests 异常原样上抛
    """
    if "path" in entry:
        data = Path(entry["path"]).read_bytes()
    else:
        resp = requests.get(entry["url"], timeout=60)
        resp.raise_for_status()
        data = resp.content
    if not data.startswith(b"%PDF"):
        raise ValueError(f"{entry['name']} 不是 PDF（首字节 {data[:8]!r}）")
    return data


def archive_raw(data: bytes, digest: str) -> Path:
    """原始字节归档到 RAW_ARCHIVE/{sha256}.pdf（存在即复用——归档件即 raw 层，不可变）。

    返回: 归档件路径
    异常: OSError — 写盘失败
    幂等: 同字节同名归档，重复调用零效果
    """
    RAW_ARCHIVE.mkdir(parents=True, exist_ok=True)
    dest = RAW_ARCHIVE / f"{digest}.pdf"
    if not dest.exists():
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
    return dest


def build_row(entry_name: str, data: bytes, ledger: FirstSeenLedger):
    """一个 PDF → ("ok", spine 行) | ("quarantine", 记录)。

    身份：source_id = sha256(原始字节)（registry 声明；同文件重复登记 = 同一 doc，天然幂等）。
    提取失败/空文本/疑似扫描（warnings 非空且文本为空）→ quarantine。
    """
    digest = hashlib.sha256(data).hexdigest()
    result = extract_pdf_from_bytes(data)
    if result.empty:
        return "quarantine", {
            "name": entry_name, "reason": "extraction_empty",
            "sha256": digest, "warnings": result.warnings or ["no_text"],
        }

    reg = _registry()
    stats = text_stats(result.text)
    doc_id = ids.spine_doc_id(SOURCE, digest)
    archived = RAW_ARCHIVE / f"{digest}.pdf"
    # 测试会把归档目录注入到 tmp，此时存绝对路径；仓库内才存相对路径（raw_uri 可解析优先）
    raw_uri = str(archived.relative_to(ROOT)) if archived.is_relative_to(ROOT) else str(archived)
    return "ok", {
        "doc_id": doc_id,
        "schema_version": _schema_version(),
        "source": SOURCE,
        "source_id": digest,
        "url": None,
        "canonical_url": None,
        "content_hash": ids.content_hash(result.text),
        "content": result.text,
        "content_format": reg["content_format"],
        "raw_uri": raw_uri,
        "original_encoding": None,
        "language": _policy()["language_default"],
        "char_count": stats["char_count"],
        "cjk_char_count": stats["cjk_char_count"],
        "utf8_byte_count": stats["utf8_byte_count"],
        "source_timestamp": None,
        "timestamp_source": "none",
        "first_seen_at": ledger.resolve(doc_id),
        "genre": reg["default_genre"],
        "license": reg["license"],
        "license_status": reg["license_status"],
        "commercial_use": reg["commercial_use"],
        "source_metadata": {"page_title": None, "publisher": None, "section": None},
    }


def extract_pdf_from_bytes(data: bytes):
    """字节 → ExtractResult（落临时文件后走统一提取路径）。"""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        tf.write(data)
        tmp_path = Path(tf.name)
    try:
        return extract_pdf(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def run(entries: list | None = None, force: bool = False, output_dir=None,
        ledger_path=None, runs_path=None) -> dict:
    """pdf ingest：清单 → 取字节+归档 → 提取+分类 → run_stage 幂等落盘。

    参数: entries — 源清单（None 读 configs/pdf_sources.json）
    返回: 同 oscar.run 形状（rows/quarantined/outputs/...）
    异常: ValueError — 聚合坏文件率超护栏；fetch 的网络/魔数错误原样上抛
    幂等: run_stage 双层；归档件按内容寻址，重复登记天然去重
    """
    entries = entries if entries is not None else load_entries()
    out_root = Path(output_dir) if output_dir else OUTPUT_DIR
    out_root.mkdir(parents=True, exist_ok=True)
    # 指纹锚必须真实反映输入：显式清单落盘为 entries.json（字节稳定）后作为输入文件；
    # 直接拿固定文件当锚 = 不同清单同指纹 = 假缓存（M3 实现期自查发现）
    if entries is not None:
        fp_input = out_root / "pdf.entries.json"
        fp_input.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
    else:
        fp_input = SOURCES_CONFIG
    ledger = FirstSeenLedger(Path(ledger_path) if ledger_path else LEDGER_PATH)
    counters = {"ok": 0, "quarantined": 0}

    def transform(_input_paths, work_dir, run_id):
        ok_rows, q_rows, stats = [], [], []
        for entry in entries:
            data = fetch_bytes(entry)
            archive_raw(data, hashlib.sha256(data).hexdigest())
            fate, payload = build_row(entry["name"], data, ledger)
            (ok_rows if fate == "ok" else q_rows).append(payload)
            stats.append({"shard": entry["name"], "ok": 1 if fate == "ok" else 0,
                          "quarantined": 0 if fate == "ok" else 1})
        total = len(ok_rows) + len(q_rows)
        ratio = (len(q_rows) / total) if total else 0.0
        limit = _policy()["pdf"]["bad_file_guardrail"]
        if ratio > limit:
            raise ValueError(f"PDF 坏文件率 {ratio:.0%} 超护栏 {limit:.0%}"
                             f"（{[q['name'] for q in q_rows]}）")
        counters.update(ok=len(ok_rows), quarantined=len(q_rows))
        outputs = []
        if ok_rows:
            out = Path(work_dir) / "part-00000.parquet"
            pq.write_table(pa.Table.from_pylist(ok_rows, schema=SPINE_PARQUET_SCHEMA), out)
            outputs.append(out)
        if q_rows:
            q_out = Path(work_dir) / "quarantine-00000.parquet"
            pq.write_table(pa.Table.from_pylist(q_rows, schema=QUARANTINE_SCHEMA), q_out)
            outputs.append(q_out)
        return outputs

    result = run_stage(
        "ingest",
        transform,
        [fp_input],
        out_root,
        force=force,
        runs_path=runs_path,
        pipeline_version=PIPELINE_VERSION,
    )
    if not result["cached"]:
        ledger.commit()
    result["rows"] = counters["ok"] or None
    result["quarantined"] = counters["quarantined"] or None
    result["outputs"] = sorted(result["output_hashes"])
    return result
