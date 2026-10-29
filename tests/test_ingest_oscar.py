"""ingest/oscar.py + ingest/sampling.py 验收测试（M2）

覆盖：抽样确定性、mock 确定性、classify_row 四路路由、护栏 fail-fast、
run 双层幂等、quarantine 产物落盘、registry 字段零字面量。"""

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from common import ids
from common.first_seen import FirstSeenLedger
from ingest import oscar, sampling

CLOCK = "2026-10-30T00:00:00+00:00"


@pytest.fixture
def ledger(tmp_path):
    return FirstSeenLedger(tmp_path / "fs.json", clock=lambda: CLOCK)


@pytest.fixture
def shard(tmp_path):
    return oscar.write_mock_shards(tmp_path, n_shards=1, rows_per_shard=30, bad_ratio=0.0, seed=7)[0]


# ---------------------------------------------------------------- sampling

def test_sample_shards_deterministic():
    all_shards = [f"part_{i}.jsonl" for i in range(20)]
    a = sampling.sample_shards(all_shards, 5, seed=42)
    b = sampling.sample_shards(list(reversed(all_shards)), 5, seed=42)  # 输入顺序无关
    assert a == b
    c = sampling.sample_shards(all_shards, 5, seed=43)
    assert a != c


def test_sample_shards_rejects_bad_k():
    with pytest.raises(ValueError, match="k"):
        sampling.sample_shards(["a"], 0, 42)
    with pytest.raises(ValueError, match="k"):
        sampling.sample_shards(["a"], 2, 42)


def test_sample_manifest_is_byte_stable(tmp_path):
    p1, p2 = tmp_path / "a.json", tmp_path / "b.json"
    sampling.write_sample_manifest(["s1", "s2"], "sample_shards(seed=42,k=2)", p1)
    sampling.write_sample_manifest(["s1", "s2"], "sample_shards(seed=42,k=2)", p2)
    assert p1.read_bytes() == p2.read_bytes()


# ---------------------------------------------------------------- mock 源

def test_mock_shards_deterministic(tmp_path):
    a = oscar.write_mock_shards(tmp_path / "a", seed=11)
    b = oscar.write_mock_shards(tmp_path / "b", seed=11)
    assert [p.read_bytes() for p in a] == [p.read_bytes() for p in b]


# ---------------------------------------------------------------- classify_row

def test_good_row_maps_to_full_spine(shard, ledger):
    lines = [json.loads(l) for l in Path(shard).read_text(encoding="utf-8").splitlines() if l.strip()]
    raw = next(r for r in lines if r.get("content"))
    fate, doc = oscar.classify_row(raw, ledger, "s.jsonl", 1)
    assert fate == "ok"
    assert doc["doc_id"] == ids.spine_doc_id("oscar", raw["warc_record_id"])
    assert doc["source"] == "oscar"
    assert doc["license"] == oscar.registry()["license"]          # registry 单一来源
    assert doc["content_format"] == oscar.registry()["content_format"]
    assert doc["first_seen_at"] == CLOCK


@pytest.mark.parametrize("row,reason", [
    ({"content": "x"}, "missing_identity"),
    ({"warc_record_id": "w1", "content": ""}, "empty_content"),
    ({"warc_record_id": "w2", "content": 123}, "bad_content_type"),
    ("not-a-dict", "bad_content_type"),
])
def test_bad_rows_go_to_quarantine(row, reason, ledger):
    fate, q = oscar.classify_row(row, ledger, "s.jsonl", 3)
    assert fate == "quarantine"
    assert q["reason"] == reason
    assert q["shard"] == "s.jsonl" and q["line_no"] == 3


def test_language_falls_back_to_policy_default(ledger):
    fate, doc = oscar.classify_row({"warc_record_id": "w9", "content": "正文", "language": None}, ledger, "s", 1)
    assert doc["language"] == oscar.policy()["oscar"]["language_default"]


# ---------------------------------------------------------------- 护栏与 run

def test_guardrail_trips_on_structural_drift(tmp_path, ledger):
    bad = tmp_path / "drift.jsonl"
    rows = [{"no_expected_field": i} for i in range(10)]  # 全部缺身份：结构漂移
    bad.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="护栏"):
        oscar._write_shard(bad, tmp_path / "o.parquet", tmp_path / "q.parquet", ledger)


def test_run_is_idempotent_and_quarantines(tmp_path):
    kwargs = dict(output_dir=tmp_path / "raw", ledger_path=tmp_path / "fs.json",
                  runs_path=tmp_path / "runs.jsonl", mock_dir=tmp_path / "mock")
    oscar.write_mock_shards(tmp_path / "mock", n_shards=2, rows_per_shard=40,
                            bad_ratio=0.08, seed=42)  # 8% < 护栏 5%? → 0.08 超护栏!
    # bad_ratio 0.08 > 0.05 会触护栏；改用护栏内的比例
    for p in (tmp_path / "mock").glob("*.jsonl"):
        p.unlink()
    oscar.write_mock_shards(tmp_path / "mock", n_shards=2, rows_per_shard=40, bad_ratio=0.04, seed=42)

    first = oscar.run(**kwargs)
    assert first["cached"] is False
    assert first["rows"] > 0 and first["quarantined"] > 0

    out = first["output_dir"]
    spine_parts = sorted(out.glob("part-*.parquet"))
    q_parts = sorted(out.glob("quarantine-*.parquet"))
    assert spine_parts and q_parts
    q_table = pq.read_table(q_parts[0])
    assert set(q_table.column("reason").to_pylist()) <= {"missing_identity", "empty_content", "bad_content_type"}

    second = oscar.run(**kwargs)
    assert second["cached"] is True and second["rows"] is None

    events = [json.loads(l) for l in (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    notes = [e["note"] for e in events if e["event"] == "finish"]
    assert notes == ["executed", "cached"]
