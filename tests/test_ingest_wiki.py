"""ingest/wiki.py 的验收测试（T5，三层验收的前两层 + 接缝纪律）

三层验收在本文件里的对应：
1. 单测：doc_id 同一条记录算两次一致；golden record 断言前 3 条映射后的**完整** spine 字典
2. 幂等：run() 跑两遍，第二遍 cached=True、transform 零执行；force 重跑字节一致
3. W1 终极验收（mc cp + 外部表 + GROUP BY）是手工步骤，见交付说明，不在 pytest 里

另外两条"接缝没漏"的守卫：
- 适配器源码里不许出现 registry 的字面量（license / content_format）
- 产出的行只许出现 schema 里声明过的 spine 键
"""

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from common import ids
from common.first_seen import FirstSeenLedger
from ingest import wiki

ROOT = Path(__file__).resolve().parent.parent
FIXED_CLOCK = "2026-10-29T10:00:00+00:00"

FIXTURE_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string()),
        pa.field("url", pa.string()),
        pa.field("title", pa.string()),
        pa.field("text", pa.string()),
    ]
)

TEXT_1 = "数学，是研究數量的一門学科。"
TEXT_2 = "哲學研究存在、知识、价值。"
TEXT_3 = "文學是一種語言藝術。"

ROWS = [
    {"id": "13", "url": "https://zh.wikipedia.org/wiki/%E6%95%B0%E5%AD%A6", "title": "数学", "text": TEXT_1},
    {"id": "18", "url": "https://zh.wikipedia.org/wiki/%E5%93%B2%E5%AD%A6", "title": "哲学", "text": TEXT_2},
    {"id": "21", "url": "https://zh.wikipedia.org/wiki/%E6%96%87%E5%AD%B8", "title": "文學", "text": TEXT_3},
]


def _write_shard(path, rows=ROWS, schema=FIXTURE_SCHEMA):
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
    return path


@pytest.fixture
def shard(tmp_path):
    return _write_shard(tmp_path / "train-00000-of-00006.parquet")


@pytest.fixture
def ledger(tmp_path):
    return FirstSeenLedger(tmp_path / "first_seen.json", clock=lambda: FIXED_CLOCK)


# ---------------------------------------------------------------------------
# 第一层：单测 —— 身份 + golden record
# ---------------------------------------------------------------------------

def test_doc_id_is_deterministic_across_calls(shard, tmp_path):
    """同一条记录调两次、甚至换一本台账，doc_id 都不许变（只由 source + page id 决定）。"""
    first = [doc["doc_id"] for doc in wiki.iter_docs(shard, ledger=FirstSeenLedger(tmp_path / "a.json", clock=lambda: "2020-01-01T00:00:00+00:00"))]
    second = [doc["doc_id"] for doc in wiki.iter_docs(shard, ledger=FirstSeenLedger(tmp_path / "b.json", clock=lambda: "2031-12-31T23:59:59+00:00"))]

    assert first == second
    assert first[0] == ids.spine_doc_id("wiki", "13")


def test_golden_record(shard, ledger):
    """前 3 条映射后的完整 spine 字典——golden 的意思就是"改一个字都要有人签字"。"""
    docs = list(wiki.iter_docs(shard, ledger=ledger))

    assert docs[0] == {
        "doc_id": ids.spine_doc_id("wiki", "13"),
        "schema_version": "0.3.2",
        "source": "wiki",
        "source_id": "13",
        "url": "https://zh.wikipedia.org/wiki/%E6%95%B0%E5%AD%A6",
        "canonical_url": None,
        "content": TEXT_1,
        "content_format": "plain_text",
        "content_hash": ids.content_hash(TEXT_1),
        "raw_uri": None,
        "original_encoding": None,
        "language": "zh",
        "char_count": 14,
        "cjk_char_count": 12,
        "utf8_byte_count": 42,
        "source_timestamp": "2023-11-01",
        "timestamp_source": "meta",
        "first_seen_at": FIXED_CLOCK,
        "genre": "wiki",
        "license": "CC BY-SA 4.0",
        "license_status": "permissive",
        "commercial_use": "allowed",
        "source_metadata": {"page_title": "数学"},
    }

    # 第二、三条只钉"与第一条不同的那几个值"，避免整块重复
    assert [doc["source_id"] for doc in docs] == ["13", "18", "21"]
    assert [doc["char_count"] for doc in docs] == [14, 13, 10]
    assert [doc["cjk_char_count"] for doc in docs] == [12, 10, 9]
    assert [doc["utf8_byte_count"] for doc in docs] == [42, 39, 30]
    assert [doc["source_metadata"]["page_title"] for doc in docs] == ["数学", "哲学", "文學"]
    assert all(doc["first_seen_at"] == FIXED_CLOCK for doc in docs)


def test_emitted_keys_are_declared_by_schema(shard, ledger):
    """spine 是 additionalProperties:false 的封闭结构——多写一个键就是契约破坏。"""
    schema = json.loads((ROOT / "schema" / "unified_schema.json").read_text(encoding="utf-8"))["spine"]

    for doc in wiki.iter_docs(shard, ledger=ledger):
        extra = set(doc) - set(schema["properties"])
        missing = set(schema["required"]) - set(doc)
        assert not extra, f"schema 未声明的键: {extra}"
        assert not missing, f"缺必填字段: {missing}"


def test_registry_fields_come_from_registry_not_code(shard):
    """接了哪个源的事实只有一个来源；适配器源码里出现字面量就是接缝漏了。"""
    registry = wiki.registry()
    source_code = (ROOT / "ingest" / "wiki.py").read_text(encoding="utf-8")

    assert "CC BY-SA" not in source_code
    assert "plain_text" not in source_code
    assert registry["content_format"] == "plain_text"
    assert registry["license"] == "CC BY-SA 4.0"

    doc = next(iter(wiki.iter_docs(shard, ledger=FirstSeenLedger(Path(shard).parent / "l.json"))))
    for key, registry_key in (
        ("license", "license"),
        ("license_status", "license_status"),
        ("commercial_use", "commercial_use"),
        ("content_format", "content_format"),
        ("language", "language"),
        ("genre", "default_genre"),
    ):
        assert doc[key] == registry[registry_key], f"{key} 没走 registry"


# ---------------------------------------------------------------------------
# 流式：按 batch 读，不整读
# ---------------------------------------------------------------------------

def test_iter_docs_is_lazy_and_batched(shard, ledger, monkeypatch):
    """取一条记录只该触发一次 iter_batches——证明整文件没被读进内存。"""
    calls = {"n": 0}
    real_parquet_file = pq.ParquetFile

    class CountingParquetFile:
        def __init__(self, path, *args, **kwargs):
            self._inner = real_parquet_file(path)

        @property
        def schema_arrow(self):
            return self._inner.schema_arrow

        def iter_batches(self, batch_size):
            calls["n"] += 1
            return self._inner.iter_batches(batch_size=batch_size)

    monkeypatch.setattr(wiki.pq, "ParquetFile", CountingParquetFile)

    docs = wiki.iter_docs(shard, ledger=ledger, batch_size=1)
    assert next(docs)["source_id"] == "13"
    assert calls["n"] == 1


def test_missing_required_column_fails_fast(tmp_path):
    """源契约变了要立刻炸，不能悄悄产出字段缺失的 spine 行。"""
    broken = tmp_path / "broken.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [{"id": "1", "url": "u", "title": "t"}],
            schema=pa.schema([("id", pa.string()), ("url", pa.string()), ("title", pa.string())]),
        ),
        broken,
    )
    with pytest.raises(ValueError, match="缺少必需列"):
        list(wiki.iter_docs(broken, ledger=FirstSeenLedger(tmp_path / "l.json")))


def test_row_without_page_id_fails_fast(tmp_path):
    """没有主键：既不能丢（raw 一条不删），也不能发明 ID → 停工。"""
    path = tmp_path / "no-id.parquet"
    _write_shard(path, rows=[{"id": None, "url": "u", "title": "t", "text": "正文"}])
    with pytest.raises(ValueError, match="缺页面 ID"):
        list(wiki.iter_docs(path, ledger=FirstSeenLedger(tmp_path / "l.json")))


# ---------------------------------------------------------------------------
# 第二层：幂等 —— 处理级（run_stage）与下载级（hf_hub_download）的咬合点
# ---------------------------------------------------------------------------

def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_run_is_idempotent(shard, tmp_path, monkeypatch):
    monkeypatch.setattr(wiki, "download", lambda n_shards=1: [shard])
    out_dir = tmp_path / "raw"
    runs_log = tmp_path / "runs.jsonl"
    kwargs = dict(output_dir=out_dir, ledger_path=tmp_path / "first_seen.json", runs_path=runs_log)

    first = wiki.run(**kwargs)
    assert first["cached"] is False
    assert first["rows"] == 3
    assert first["outputs"] == ["part-00000.parquet"]
    produced = out_dir / "ingest" / "part-00000.parquet"
    assert produced.is_file()
    assert pq.ParquetFile(produced).metadata.num_rows == 3

    # 产物 schema 是我们显式声明的那份，不是 pyarrow 猜的
    assert pq.ParquetFile(produced).schema_arrow.names == [f.name for f in wiki.PARQUET_SCHEMA]

    # 第二遍：cached，transform 零执行（rows=None 就是没跑过的证据）
    second = wiki.run(**kwargs)
    assert second["cached"] is True
    assert second["rows"] is None
    assert _sha256(produced) == first["output_hashes"]["part-00000.parquet"]

    events = [json.loads(line) for line in runs_log.read_text(encoding="utf-8").splitlines()]
    notes = [event["note"] for event in events if event["event"] == "finish"]
    assert notes == ["executed", "cached"]


def test_forced_rerun_is_byte_identical(shard, tmp_path, monkeypatch):
    """force 重跑（真的重新计算）也必须字节一致——first_seen_at 走台账，不重新取时钟。"""
    monkeypatch.setattr(wiki, "download", lambda n_shards=1: [shard])
    out_dir = tmp_path / "raw"
    kwargs = dict(output_dir=out_dir, ledger_path=tmp_path / "first_seen.json", runs_path=tmp_path / "runs.jsonl")

    first = wiki.run(**kwargs)
    forced = wiki.run(force=True, **kwargs)

    assert forced["cached"] is False
    assert forced["output_hashes"] == first["output_hashes"]


def test_first_seen_ledger_is_written_once(shard, tmp_path, monkeypatch):
    monkeypatch.setattr(wiki, "download", lambda n_shards=1: [shard])
    ledger_path = tmp_path / "first_seen.json"
    kwargs = dict(output_dir=tmp_path / "raw", ledger_path=ledger_path, runs_path=tmp_path / "runs.jsonl")

    wiki.run(**kwargs)
    stored = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert set(stored) == {ids.spine_doc_id("wiki", sid) for sid in ("13", "18", "21")}

    before = ledger_path.read_bytes()
    wiki.run(**kwargs)  # 命中缓存：台账零写入
    assert ledger_path.read_bytes() == before

    stored_times = set(stored.values())
    docs = list(wiki.iter_docs(shard, ledger=FirstSeenLedger(ledger_path)))
    assert {doc["first_seen_at"] for doc in docs} == stored_times


def test_download_rejects_out_of_range_shards():
    with pytest.raises(ValueError, match="n_shards"):
        wiki.download(0)
    with pytest.raises(ValueError, match="n_shards"):
        wiki.download(wiki.N_SHARDS + 1)
