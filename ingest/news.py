"""ingest/news.py — 中文新闻源适配器（M3，本地 jsonl 输入）（DEVLOG M3-D4）

行契约（人工策划语料，jsonl 每行）：
  {"id": str 必填, "url": str|null, "title": str|null, "content": str 必填,
   "published": "YYYY-MM-DD"|null, "publisher": str|null, "section": str|null}

策略：干净源 fail-fast（缺 id/空 content 直接 ValueError，M2-D4 家族的 wiki 侧）。
真实语料绑定待 N2（选型硬条件：免翻墙可下/有稳定主键/体裁纯；候选与代价见 MANUAL）。
本模块对「格式契约明确的 jsonl」是完整实现——N2 只替换 download()。
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Iterator

import pyarrow as pa
import pyarrow.parquet as pq

from common import ids
from common.first_seen import FirstSeenLedger
from common.idempotent import run_stage
from common.textstats import text_stats
from ingest.spine_schema import SPINE_PARQUET_SCHEMA

ROOT = Path(__file__).resolve().parent.parent
SOURCE = "news"
PIPELINE_VERSION = "ingest-news-0.1.0"
DATA_FILE = ROOT / "data" / "mock" / "news" / "news.jsonl"
OUTPUT_DIR = ROOT / "data" / "raw" / "news"
LEDGER_PATH = ROOT / "data" / "meta" / "first_seen" / f"{SOURCE}.json"


@lru_cache(maxsize=1)
def _registry():
    reg = json.loads((ROOT / "configs" / "source_registry.json").read_text(encoding="utf-8"))
    return reg["sources"][SOURCE]


@lru_cache(maxsize=1)
def _schema_version():
    return json.loads((ROOT / "schema" / "unified_schema.json").read_text(encoding="utf-8"))["version"]


def write_mock_lines(path: Path, n: int = 30, seed: int = 7) -> Path:
    """生成确定性 mock 新闻 jsonl（N2 前的输入源）。同 seed 同字节。"""
    import random

    rng = random.Random(seed)
    rows = []
    for i in range(n):
        rows.append(json.dumps({
            "id": f"news-{seed}-{i:04d}",
            "url": f"https://news.example.org/a/{i}",
            "title": f"第{i}号模拟新闻标题",
            "content": f"这是第{i}条模拟新闻正文。" + "经济数据昨日发布，市场反应平稳。" * rng.randint(1, 3),
            "published": f"2026-0{rng.randint(1, 9)}-{rng.randint(10, 28)}",
            "publisher": ["新华源", "人民源", "澎湃源"][i % 3],
            "section": ["财经", "时政", "科技"][i % 3],
        }, ensure_ascii=False))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def iter_docs(path, ledger: FirstSeenLedger) -> Iterator[dict]:
    """逐行 jsonl → spine 字典（流式）。缺 id/空 content → ValueError（fail-fast，干净源契约）。"""
    reg = _registry()
    with Path(path).open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            text = line.strip()
            if not text:
                continue
            row = json.loads(text)
            rid, content = row.get("id"), row.get("content")
            if not isinstance(rid, str) or not rid:
                raise ValueError(f"{Path(path).name}:{line_no} 缺 id——策划语料契约破坏，停工")
            if not isinstance(content, str) or not content:
                raise ValueError(f"{Path(path).name}:{line_no} 空 content——停工")
            stats = text_stats(content)
            doc_id = ids.spine_doc_id(SOURCE, rid)
            yield {
                "doc_id": doc_id,
                "schema_version": _schema_version(),
                "source": SOURCE,
                "source_id": rid,
                "url": row.get("url"),
                "canonical_url": None,
                "content_hash": ids.content_hash(content),
                "content": content,
                "content_format": reg["content_format"],
                "raw_uri": None,
                "original_encoding": None,
                "language": "zh",
                "char_count": stats["char_count"],
                "cjk_char_count": stats["cjk_char_count"],
                "utf8_byte_count": stats["utf8_byte_count"],
                "source_timestamp": row.get("published"),
                "timestamp_source": "meta" if row.get("published") else "none",
                "first_seen_at": ledger.resolve(doc_id),
                "genre": reg["default_genre"],
                "license": reg["license"],
                "license_status": reg["license_status"],
                "commercial_use": reg["commercial_use"],
                "source_metadata": {"page_title": row.get("title"),
                                    "publisher": row.get("publisher"),
                                    "section": row.get("section")},
            }


def run(data_file: Path | None = None, force: bool = False, output_dir=None,
        ledger_path=None, runs_path=None) -> dict:
    """news ingest：jsonl → run_stage 幂等落盘（wiki 同款双层幂等）。"""
    src = Path(data_file) if data_file else DATA_FILE
    if not src.exists():
        write_mock_lines(src)
    ledger = FirstSeenLedger(Path(ledger_path) if ledger_path else LEDGER_PATH)
    counters = {"rows": 0}

    def transform(input_paths, work_dir, run_id):
        out_path = Path(work_dir) / "part-00000.parquet"
        rows = list(iter_docs(input_paths[0], ledger))
        pq.write_table(pa.Table.from_pylist(rows, schema=SPINE_PARQUET_SCHEMA), out_path)
        counters["rows"] = len(rows)
        return [out_path]

    result = run_stage("ingest", transform, [src],
                       Path(output_dir) if output_dir else OUTPUT_DIR,
                       force=force, runs_path=runs_path, pipeline_version=PIPELINE_VERSION)
    if not result["cached"]:
        ledger.commit()
    result["rows"] = counters["rows"] or None
    result["outputs"] = sorted(result["output_hashes"])
    return result


def main(argv=None):  # pragma: no cover
    """python -m ingest.news —— 家族统一 CLI 入口（M3 补：漏写它导致 -m 静默空跑的教训）。"""
    import sys

    from common import log

    log.setup()
    result = run(force="--force" in (sys.argv[1:] if argv is None else argv))
    print(json.dumps({k: v for k, v in result.items() if k != "shards"},
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()
