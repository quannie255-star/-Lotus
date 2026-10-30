"""ingest/oscar.py — OSCAR-2201 zh 适配器（M2，mock 骨架，真实数据待 N1）（DEVLOG M2-D3）

== 阻塞声明（不许忘）==
HF 仓只有 *_meta 分片，正文在 OSCAR S3；取 meta 文件需 huggingface.co 直连（FlClash）。
本模块按「假设行结构」跑通全骨架；N1 实测后仅需重写 classify_row 的字段读取与
download() 的取数逻辑——隔离边界就是为此设计的。

假设行结构（mock 依此生成，N1 校准）：
  {"warc_record_id": str, "content": str, "url": str|null, "language": str|null}

== 身份与坏行（对应 owner 三答 + 导师点评，DEVLOG M2-D3/D4）==
- 身份：warc_record_id（registry 声明的稳定主键）。同页两次爬取 = 两个 warc id =
  两条 raw 记录（爬取事实保留）；内容层面的折叠交给 M5 dedup 的内容哈希。
- 坏行（宪法第 7 条豁免记录）：无法构成 spine 的行不丢弃、不阻断——quarantine 为
  一等公民（独立 parquet，可查询可复核可放回）。单分片坏行率超护栏（5%）则整片
  fail-fast：区分「毒丸行」（局部）与「源结构漂移」（全局）。
- 注意与 wiki 的差异：wiki 缺主键直接停（干净源，一行异常=契约破坏）；oscar 隔离
  （脏源，行级异常=常态）。策略差异入 configs/ingest_policy.json，不是代码分支。
"""

import hashlib
import json
import random
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
SOURCE = "oscar"
PIPELINE_VERSION = "ingest-oscar-0.1.0-mock"
BATCH_SIZE = 10_000

MOCK_DIR = ROOT / "data" / "mock" / "oscar"
OUTPUT_DIR = ROOT / "data" / "raw" / "oscar"
LEDGER_PATH = ROOT / "data" / "meta" / "first_seen" / f"{SOURCE}.json"

QUARANTINE_SCHEMA = pa.schema(
    [
        pa.field("shard", pa.string(), nullable=False),
        pa.field("line_no", pa.int64(), nullable=False),
        pa.field("reason", pa.string(), nullable=False),
        pa.field("raw_json", pa.string()),   # 原始行截断保留（≤2000 字），复核用
    ]
)


@lru_cache(maxsize=1)
def policy() -> dict:
    return json.loads((ROOT / "configs" / "ingest_policy.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def registry() -> dict:
    reg = json.loads((ROOT / "configs" / "source_registry.json").read_text(encoding="utf-8"))
    return reg["sources"][SOURCE]


@lru_cache(maxsize=1)
def schema_version() -> str:
    return json.loads((ROOT / "schema" / "unified_schema.json").read_text(encoding="utf-8"))["version"]


# ---------------------------------------------------------------------------
# mock 源：确定性生成"好行为主、坏行参差"的分片。N1 之后整个小节删除。
# ---------------------------------------------------------------------------

_GOOD_TEXTS = [
    "这是一段模拟的中文网页正文，包含足够的汉字用于通过最小长度检查。",
    "另一个模拟页面：数据管道的确定性意味着同样的输入永远得到同样的输出。",
    "爬虫语料的典型形态：正文、导航残留与广告文字混在一起，等待清洗阶段处理。",
]
_BAD_RECIPES = ("empty_content", "missing_identity", "bad_content_type")


def write_mock_shards(out_dir: Path, n_shards: int = 4, rows_per_shard: int = 50,
                      bad_ratio: float = 0.03, seed: int = 42) -> list:
    """bad_ratio 默认 0.03：必须显著低于护栏 0.05——默认参数自己撞护栏 = CLI 入口坏掉
    （2026-10-30 实跑教训：0.1 时单片 3/50=6% 的随机波动就触发 fail-fast）。"""
    """生成 mock 分片（jsonl）。确定性：同 seed 同字节。

    参数: out_dir — 输出目录；n_shards/rows_per_shard — 规模；bad_ratio — 坏行占比；seed — 种子
    返回: 分片文件路径列表（有序）
    异常: ValueError — bad_ratio 不在 [0,1)
    幂等: 同参数重跑覆盖产出相同字节（random 全部由 seed 驱动）
    """
    if not 0 <= bad_ratio < 1:
        raise ValueError(f"bad_ratio 必须在 [0,1)，收到 {bad_ratio}")
    rng = random.Random(seed)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for shard_idx in range(n_shards):
        path = out_dir / f"oscar_part_{shard_idx + 1}.jsonl"
        lines = []
        for row_idx in range(rows_per_shard):
            if rng.random() < bad_ratio:
                recipe = _BAD_RECIPES[rng.randrange(len(_BAD_RECIPES))]
                if recipe == "empty_content":
                    row = {"warc_record_id": f"warc-{seed}-{shard_idx}-{row_idx}", "content": "", "url": None}
                elif recipe == "missing_identity":
                    row = {"content": rng.choice(_GOOD_TEXTS), "url": "https://example.org/x"}
                else:
                    row = {"warc_record_id": f"warc-{seed}-{shard_idx}-{row_idx}",
                           "content": 12345, "url": "https://example.org/y"}
            else:
                row = {
                    "warc_record_id": f"warc-{seed}-{shard_idx}-{row_idx}",
                    "content": rng.choice(_GOOD_TEXTS) + f"（第{row_idx}条）",
                    "url": f"https://example.org/page-{shard_idx}-{row_idx}",
                    "language": "zh" if rng.random() < 0.9 else "en",
                }
            lines.append(json.dumps(row, ensure_ascii=False))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# 行分类：N1 实测后唯一需要重写的接缝
# ---------------------------------------------------------------------------

def classify_row(raw: dict, ledger: FirstSeenLedger, shard_name: str, line_no: int):
    """一行 → ("ok", spine_dict) | ("quarantine", q_record)。

    参数: raw — 解析后的 jsonl 行；ledger — 首见台账；shard_name/line_no — quarantine 定位用
    返回: 二元组（见上）；quarantine 记录含 reason ∈ {missing_identity, empty_content, bad_content_type}
    异常: 不抛——行级问题一律转 quarantine（与 wiki 的 fail-fast 策略差异见模块头）
    幂等: 纯函数 + 台账读取；同输入同输出
    """
    if not isinstance(raw, dict):
        return "quarantine", {"shard": shard_name, "line_no": line_no,
                              "reason": "bad_content_type", "raw_json": str(raw)[:2000]}
    warc_id = raw.get("warc_record_id")
    if not isinstance(warc_id, str) or not warc_id:
        return "quarantine", {"shard": shard_name, "line_no": line_no, "reason": "missing_identity",
                              "raw_json": json.dumps(raw, ensure_ascii=False)[:2000]}
    content = raw.get("content")
    if not isinstance(content, str):
        return "quarantine", {"shard": shard_name, "line_no": line_no, "reason": "bad_content_type",
                              "raw_json": json.dumps(raw, ensure_ascii=False)[:2000]}
    if not content:
        return "quarantine", {"shard": shard_name, "line_no": line_no, "reason": "empty_content",
                              "raw_json": json.dumps(raw, ensure_ascii=False)[:2000]}

    reg, pol = registry(), policy()["oscar"]
    stats = text_stats(content)
    source_id = warc_id
    return "ok", {
        "doc_id": ids.spine_doc_id(SOURCE, source_id),
        "schema_version": schema_version(),
        "source": SOURCE,
        "source_id": source_id,
        "url": raw.get("url"),
        "canonical_url": None,
        "content_hash": ids.content_hash(content),
        "content": content,
        "content_format": reg["content_format"],
        "raw_uri": None,
        "original_encoding": None,
        "language": raw.get("language") or pol["language_default"],
        "char_count": stats["char_count"],
        "cjk_char_count": stats["cjk_char_count"],
        "utf8_byte_count": stats["utf8_byte_count"],
        "source_timestamp": None,
        "timestamp_source": "none",
        "first_seen_at": ledger.resolve(ids.spine_doc_id(SOURCE, source_id)),
        "genre": reg["default_genre"],
        "license": reg["license"],
        "license_status": reg["license_status"],
        "commercial_use": reg["commercial_use"],
        "source_metadata": {"page_title": None, "publisher": None, "section": None},
    }


def iter_lines(path: Path) -> Iterator[tuple[int, str]]:
    """逐行读 jsonl，产出 (行号, 行文本)；坏 JSON 转为 quarantine 行而非中断。"""
    with Path(path).open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            text = line.strip()
            if text:
                yield line_no, text


def _write_shard(input_path: Path, out_path: Path, q_path: Path, ledger) -> dict:
    """一个分片 → spine part + quarantine part。返回计数（不判护栏——护栏是 run 级语义）。"""
    ok_rows, q_rows = [], []
    for line_no, text in iter_lines(input_path):
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            raw = None
        fate, payload = classify_row(raw if raw is not None else text, ledger, input_path.name, line_no)
        (ok_rows if fate == "ok" else q_rows).append(payload)

    if ok_rows:
        pq.write_table(pa.Table.from_pylist(ok_rows, schema=SPINE_PARQUET_SCHEMA), out_path)
    if q_rows:
        pq.write_table(pa.Table.from_pylist(q_rows, schema=QUARANTINE_SCHEMA), q_path)
    return {"shard": input_path.name, "ok": len(ok_rows), "quarantined": len(q_rows)}


def _check_guardrail(stats: list) -> float:
    """run 级聚合护栏：总坏行率超阈值 → 整 run fail-fast。

    为什么聚合而不是每片独立判：小分片（mock 50 行）的比率方差大到 3% 的真实坏行率都有
    ~55% 概率单片超 5%（二项分布，2026-10-30 实跑教训）；聚合让护栏在 mock 与真实分片
    （十万行级，方差可忽略）下语义一致。「结构漂移」本来就是 run 级属性。

    参数: stats — 每片 {shard, ok, quarantined} 计数列表
    返回: 聚合坏行率（不超阈值时）
    异常: ValueError — 聚合比率超 configs/ingest_policy.json 的 bad_row_guardrail，消息含分片明细
    """
    total = sum(s["ok"] + s["quarantined"] for s in stats) or 1
    quarantined = sum(s["quarantined"] for s in stats)
    ratio = quarantined / total
    limit = policy()["oscar"]["bad_row_guardrail"]
    if ratio > limit:
        detail = ", ".join(f"{s['shard']}:{s['quarantined']}/{s['ok'] + s['quarantined']}" for s in stats)
        raise ValueError(
            f"坏行率 {ratio:.1%} 超护栏 {limit:.0%}（{quarantined}/{total}；分片: {detail}）"
            "——疑似源结构漂移，整 run 拒绝而非静默产出"
        )
    return ratio


def download(n_shards: int | None = None) -> list:
    """⚠️ mock 源：确保 mock 分片存在并返回路径。N1 实测后重写为
    hf_hub_download(meta 分片) + S3 正文拼装（依赖阻塞：FlClash）。

    参数: n_shards — 忽略（mock 固定 4 片）；保留签名与家族契约一致
    返回: mock 分片路径列表
    异常: OSError — 生成失败
    幂等: 同 seed 重写字节不变；已存在则直接复用
    """
    paths = sorted(MOCK_DIR.glob("oscar_part_*.jsonl"))
    if not paths:
        paths = write_mock_shards(MOCK_DIR)
    return paths


def run(force: bool = False, output_dir=None, ledger_path=None, runs_path=None,
        mock_dir: Path | None = None) -> dict:
    """oscar ingest：mock download → 分片级处理（护栏+quarantine）→ run_stage 幂等落盘。

    返回: {"cached","run_id","output_dir","output_hashes","shards","rows","quarantined","ratio"}
    异常: ValueError — 护栏触发；其余同 run_stage
    幂等: 同 wiki 双层幂等；quarantine 产物同样进 output_hashes 参与验货
    """
    if mock_dir is not None:
        global MOCK_DIR
        old, MOCK_DIR = MOCK_DIR, Path(mock_dir)
        try:
            shards = download()
        finally:
            MOCK_DIR = old
    else:
        shards = download()

    ledger = FirstSeenLedger(Path(ledger_path) if ledger_path else LEDGER_PATH)
    counters = {"quarantined": 0, "ok": 0}

    def transform(input_paths, work_dir, run_id):
        outputs, all_stats = [], []
        for index, input_path in enumerate(sorted((Path(p) for p in input_paths), key=str)):
            out_path = Path(work_dir) / f"part-{index:05d}.parquet"
            q_path = Path(work_dir) / f"quarantine-{index:05d}.parquet"
            stats = _write_shard(input_path, out_path, q_path, ledger)
            all_stats.append(stats)
            counters["ok"] += stats["ok"]
            counters["quarantined"] += stats["quarantined"]
            outputs.extend([p for p in (out_path, q_path) if p.exists()])
        counters["ratio"] = _check_guardrail(all_stats)
        return outputs

    result = run_stage(
        "ingest",
        transform,
        shards,
        Path(output_dir) if output_dir else OUTPUT_DIR,
        force=force,
        runs_path=runs_path,
        pipeline_version=PIPELINE_VERSION,
    )
    if not result["cached"]:
        ledger.commit()
    result["shards"] = [str(p) for p in shards]
    result["rows"] = counters["ok"] or None
    result["quarantined"] = counters["quarantined"] or None
    result["outputs"] = sorted(result["output_hashes"])
    return result


def main(argv=None):  # pragma: no cover
    import sys

    from common import log

    log.setup()
    result = run(force="--force" in (sys.argv[1:] if argv is None else argv))
    print(json.dumps({k: v for k, v in result.items() if k != "shards"},
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()
