"""ingest/wiki.py — Wikipedia（zh, 20231101 dump）ingest 适配器（T5）

== 这个模块的定位：薄接缝 ==

四个源长得完全不同，但 ingest 对下游的承诺只有一条：**吐出符合 spine schema 的字典流**。
所以本模块只做一件事——把 `wikimedia/wikipedia` 的 parquet 行列映射成 spine 字典：

    def iter_docs(parquet_path) -> Iterator[dict]

三条纪律（每条都对应一个已有资产）：

1. 流式，不整读 —— `Iterator` + `ParquetFile.iter_batches`，内存里永远只有一个 batch。
   单个分片 30 万行，四个源将来上百万行，`list(...)` 是等着 OOM。
2. 只映射，不清洗 —— 不过滤、不改写、不算 changelog。raw 层一条不删，clean 才有得删、
   有得记 changelog。**ingest 零 changelog，changelog 从 clean 开始。**
3. 身份/指纹来自 `common.ids`，许可/体裁来自 `source_registry` —— 本文件里不许出现
   许可名、体裁、格式、语言这类字面量；registry 是唯一真源（见 configs/source_registry.json）。

== 两级幂等（不重复造轮子） ==

    hf_hub_download（下载级：断点续传 + 本地缓存 + 校验，免费）
        → run_stage("ingest", ...)（处理级：输入指纹 → 跳过计算，T4 造的）
                              ↑ 输入指纹就是「已下载的分片文件」，天然咬合

落盘布局（本地两段式，再 mc cp 上传）：

    data/raw/wiki/ingest/part-00000.parquet        ← 本模块产物（run_stage 的 stage 目录）
    s3a://raw/docs/source=wiki/part-00000.parquet  ← mc cp 之后（hive 式分区目录）

    为什么不直接落在桶根：Hive 4 拒绝把 `s3a://raw/` 当表位置
    （`location must not be root path`），所以对象存储侧统一多一级 `docs/` 前缀。
    详情见 docs/ingest-wiki.md 第八节。

== 字段取值的三条约定（"未检测" ≠ "空"） ==

- **未检测**：raw 阶段没有信息 → 字段**不存在**（`language_mix` / `dominant_script` /
  `language_confidence`）。写 `{}` 或 `0.0` 是撒谎——那是在说"检测过了，结果是空/零"。
- **确认为无**：schema 允许 null 且我们能断言源里没有 → 显式 `null`
  （`canonical_url`：URL 归一是 clean/后续阶段的事；`raw_uri`：wiki 没有保留原始文件；
  `original_encoding`：Arrow 字符串已归一成 UTF-8，原始字节编码不可知，不冒充 UTF-8）。
- **有值**：语言 `zh` 来自源声明（registry），不是检测出来的，所以不写 confidence。

== first_seen_at 为什么要台账 ==

schema policy.time 要求「重跑沿用旧值，参与幂等校验」。若每跑一次写 now()，
T4 的"同样输入两次跑出的文件字节一致"当场失效。所以首次入库时间落在
`data/meta/first_seen/wiki.json`（`common/first_seen.py`），重跑读旧值。

== 为什么行里没有 ingest_run_id ==

stage_extensions.ingest 里有 ingest_run_id，但把它写进行 = 行内容随运行而变 = 字节级
确定性报废。行的归因走 `data/meta/runs.jsonl`（start/finish 对）+ manifest（输入指纹/产物哈希），
数据行只承载事实，不承载运行上下文。
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Iterator

import pyarrow as pa
import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download

from common import ids
from common.first_seen import FirstSeenLedger
from common.idempotent import run_stage
from common.textstats import text_stats

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "source_registry.json"
SCHEMA_PATH = ROOT / "schema" / "unified_schema.json"

SOURCE = "wiki"

# ---- 源坐标：HF 镜像 wikimedia/wikipedia 的 20231101.zh 分片（6 个现成 parquet，无需 XML dump）----
REPO_ID = "wikimedia/wikipedia"
REPO_TYPE = "dataset"
DUMP_DATE = "20231101"
DUMP_DIR = f"{DUMP_DATE}.zh"
N_SHARDS = 6
SHARD_FILENAME = DUMP_DIR + "/train-{index:05d}-of-00006.parquet"

# 源内事件时间：dump 日期（date 粒度，schema 允许 date-time/date/year-month 三形态）
SOURCE_TIMESTAMP = "2023-11-01"
TIMESTAMP_SOURCE = "meta"

PIPELINE_VERSION = "ingest-wiki-0.1.0"
BATCH_SIZE = 10_000

OUTPUT_DIR = ROOT / "data" / "raw" / "wiki"
LEDGER_PATH = ROOT / "data" / "meta" / "first_seen" / f"{SOURCE}.json"

# 源列 → 必需列清单。少一列就直接炸：宁可停工，也不产出悄悄缺字段的 spine 行。
REQUIRED_COLUMNS = ("id", "url", "title", "text")

# 产物 schema：显式声明而不是让 pyarrow 从第一批数据猜——否则某一批全空字段会改 schema，
# 同一批输入跑出两种文件。顺序 = unified_schema.spine 的顺序（未检测的字段不出现）。
PARQUET_SCHEMA = pa.schema(
    [
        pa.field("doc_id", pa.string(), nullable=False),
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("source_id", pa.string(), nullable=False),
        pa.field("url", pa.string()),
        pa.field("canonical_url", pa.string()),
        pa.field("content_hash", pa.string(), nullable=False),
        pa.field("content", pa.string(), nullable=False),
        pa.field("content_format", pa.string(), nullable=False),
        pa.field("raw_uri", pa.string()),
        pa.field("original_encoding", pa.string()),
        pa.field("language", pa.string(), nullable=False),
        pa.field("char_count", pa.int64(), nullable=False),
        pa.field("cjk_char_count", pa.int64(), nullable=False),
        pa.field("utf8_byte_count", pa.int64(), nullable=False),
        pa.field("source_timestamp", pa.string()),
        pa.field("timestamp_source", pa.string(), nullable=False),
        pa.field("first_seen_at", pa.string(), nullable=False),
        pa.field("genre", pa.string(), nullable=False),
        pa.field("license", pa.string(), nullable=False),
        pa.field("license_status", pa.string(), nullable=False),
        pa.field("commercial_use", pa.string(), nullable=False),
        pa.field(
            "source_metadata",
            pa.struct([pa.field("page_title", pa.string())]),
        ),
    ]
)


# ---------------------------------------------------------------------------
# 配置与版本：不许在代码里另写一份事实
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def registry():
    """当前源的 registry 条目（许可、体裁、格式、语言、ID 策略的唯一真源）。"""
    with REGISTRY_PATH.open(encoding="utf-8") as f:
        return json.load(f)["sources"][SOURCE]


@lru_cache(maxsize=1)
def schema_version():
    """spine 行声称自己遵循的 schema 版本，直接读 schema 文件，不在代码里写死。"""
    with SCHEMA_PATH.open(encoding="utf-8") as f:
        return json.load(f)["version"]


# ---------------------------------------------------------------------------
# 下载级幂等：hf_hub_download 自带缓存 + 断点续传 + 校验
# ---------------------------------------------------------------------------

def download(n_shards: int = 1) -> list:
    """把 20231101.zh 的前 n_shards 个分片拉到本地 HF 缓存，返回路径列表。

    断点续传与缓存命中由 hf_hub_download 负责——这就是"下载级幂等不必自己造"的意思。
    """
    if not 1 <= n_shards <= N_SHARDS:
        raise ValueError(f"n_shards 必须在 1..{N_SHARDS} 之间，收到 {n_shards}")

    return [
        Path(
            hf_hub_download(
                repo_id=REPO_ID,
                repo_type=REPO_TYPE,
                filename=SHARD_FILENAME.format(index=index),
            )
        )
        for index in range(n_shards)
    ]


# ---------------------------------------------------------------------------
# 处理级映射：源行 → spine 字典
# ---------------------------------------------------------------------------

def _require_columns(parquet_path):
    """缺列是源契约变了，不是数据脏——直接炸，并说清缺哪几列。"""
    names = set(pq.ParquetFile(parquet_path).schema_arrow.names)
    missing = [name for name in REQUIRED_COLUMNS if name not in names]
    if missing:
        raise ValueError(f"{Path(parquet_path).name} 缺少必需列 {missing}，实际列 = {sorted(names)}")


def map_row(row, ledger):
    """一条源行 → 一条 spine 字典。纯映射 + 三个便宜计数，不做任何判断。"""
    page_id = row["id"]
    if page_id is None or str(page_id) == "":
        # 没有主键的行走不下去：既不能丢（raw 一条不删），也不能发明 ID（身份来自 common.ids）
        raise ValueError(f"源行缺页面 ID，无法确定身份: title={row.get('title')!r}")

    source_id = str(page_id)
    content = row["text"]
    if not isinstance(content, str):
        raise TypeError(f"页面 {source_id} 的 text 不是字符串: {type(content).__name__}")

    doc_id = ids.spine_doc_id(SOURCE, source_id)
    meta = registry()
    stats = text_stats(content)

    return {
        "doc_id": doc_id,
        "schema_version": schema_version(),
        "source": SOURCE,
        "source_id": source_id,
        "url": row["url"],
        "canonical_url": None,          # URL 归一是 clean/后续阶段的事，不是 ingest 的判断
        "content": content,
        "content_format": meta["content_format"],
        "content_hash": ids.content_hash(content),
        "raw_uri": None,                # wiki 走 HF 镜像，没有保留原始 dump 文件
        "original_encoding": None,      # 原始字节编码不可知，不冒充 UTF-8
        "language": meta["language"],   # 源声明 zh（registry），不是检测结果
        "char_count": stats["char_count"],
        "cjk_char_count": stats["cjk_char_count"],
        "utf8_byte_count": stats["utf8_byte_count"],
        "source_timestamp": SOURCE_TIMESTAMP,
        "timestamp_source": TIMESTAMP_SOURCE,
        "first_seen_at": ledger.resolve(doc_id),
        "genre": meta["default_genre"],
        "license": meta["license"],
        "license_status": meta["license_status"],
        "commercial_use": meta["commercial_use"],
        "source_metadata": {"page_title": row["title"]},
    }


def iter_docs(parquet_path, ledger=None, batch_size=BATCH_SIZE) -> Iterator:
    """逐 row group / batch 读 parquet，逐条 yield spine 字典（流式，不整读）。

    ledger 由调用方显式注入（"谁需要什么信息就由谁传"）。默认用一个只读台账：
    已入库的 doc 沿用旧时间，新 doc 取当前时间但**不落盘**——落盘是 run() 的事。
    """
    path = Path(parquet_path)
    _require_columns(path)
    ledger = ledger if ledger is not None else FirstSeenLedger(LEDGER_PATH)

    for batch in pq.ParquetFile(path).iter_batches(batch_size=batch_size):
        # iter_batches 默认给 RecordBatch；to_pylist() 才是 dict 列表。
        for row in batch.to_pylist():
            yield map_row(row, ledger)


# ---------------------------------------------------------------------------
# 处理级幂等 + 落盘：run_stage 包住 download → iter_docs → 写 parquet
# ---------------------------------------------------------------------------

def _write_shard(input_path, out_path, ledger):
    """一个分片 → 一个 part 文件，按 batch 追加写，内存占用与分片大小无关。"""
    written = 0
    with pq.ParquetWriter(
        out_path,
        PARQUET_SCHEMA,
        compression="zstd",
        version="2.6",
        use_dictionary=True,
        data_page_version="1.0",
        store_schema=True,
    ) as writer:
        for batch in pq.ParquetFile(input_path).iter_batches(batch_size=BATCH_SIZE):
            rows = [map_row(row, ledger) for row in batch.to_pylist()]
            writer.write_table(pa.Table.from_pylist(rows, schema=PARQUET_SCHEMA), row_group_size=BATCH_SIZE)
            written += len(rows)
    return written


def _make_transform(ledger, counts):
    """闭包只捕获显式的依赖（台账 + 计数容器），不读全局可变状态。"""

    def transform(input_paths, work_dir, run_id):
        outputs = []
        for index, input_path in enumerate(sorted((Path(p) for p in input_paths), key=str)):
            out_path = Path(work_dir) / f"part-{index:05d}.parquet"
            counts.append(_write_shard(input_path, out_path, ledger))
            outputs.append(out_path)
        return outputs

    return transform


def run(n_shards: int = 1, force: bool = False, output_dir=None, ledger_path=None, runs_path=None) -> dict:
    """跑 ingest 阶段：download（下载级幂等）→ run_stage（处理级幂等）→ 本地 parquet。

    返回 {"cached", "run_id", "output_dir", "output_hashes", "shards", "rows"}。
    第二遍同样输入：cached=True，transform 零执行，台账零写入。
    """
    shards = download(n_shards)
    ledger = FirstSeenLedger(Path(ledger_path) if ledger_path else LEDGER_PATH)
    counts = []

    result = run_stage(
        "ingest",
        _make_transform(ledger, counts),
        shards,
        Path(output_dir) if output_dir else OUTPUT_DIR,
        force=force,
        runs_path=runs_path,
        pipeline_version=PIPELINE_VERSION,
    )

    if not result["cached"]:
        ledger.commit()  # 只有产物真的落盘了，才承认这些"首次入库"时间

    result["shards"] = [str(p) for p in shards]
    result["rows"] = sum(counts) if counts else None
    result["outputs"] = sorted(result["output_hashes"])
    return result


def main(argv=None):  # pragma: no cover - 手工跑的口子
    """python -m ingest.wiki [n_shards] —— 手工跑一次，打印结果摘要。"""
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    result = run(n_shards=int(argv[0]) if argv else 1)
    print(json.dumps({k: v for k, v in result.items() if k != "shards"}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()
