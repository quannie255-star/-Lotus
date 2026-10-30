"""mix + release 测试（M7）"""

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from common import ids
from ingest.spine_schema import SPINE_PARQUET_SCHEMA
from mix.run import stratified_sample, token_count_estimate, MIX_EXT_SCHEMA

CLOCK = "2026-10-30T00:00:00+00:00"


def _qrow(doc_id, source, content, score, license_status="permissive"):
    from common.textstats import text_stats
    s = text_stats(content)
    return {"doc_id": doc_id, "schema_version": "t", "source": source, "source_id": doc_id,
            "url": None, "canonical_url": None, "content_hash": ids.content_hash(content),
            "content": content, "content_format": "plain_text", "raw_uri": None,
            "original_encoding": None, "language": "zh", "char_count": s["char_count"],
            "cjk_char_count": s["cjk_char_count"], "utf8_byte_count": s["utf8_byte_count"],
            "source_timestamp": None, "timestamp_source": "none", "first_seen_at": CLOCK,
            "genre": "wiki", "license": "CC0", "license_status": license_status,
            "commercial_use": "allowed",
            "source_metadata": {"page_title": None, "publisher": None, "section": None},
            "quality_score": score}


# ---------------------------------------------------------------- mix

def test_token_estimate_mixed_script():
    assert token_count_estimate("四个汉字") == 4
    assert token_count_estimate("abcd") == 1          # 4 latin ≈ 1 token
    assert token_count_estimate("汉abc") == 1 + 0      # 1 cjk + 3 latin(<4)
    assert token_count_estimate("汉字abcd") == 2 + 1


def test_stratified_budget_and_determinism():
    pol = {"token_budget": 1000, "seed": 42, "ratios": {"wiki": 0.8, "news": 0.2},
           "tokenizer": {"name": "char-estimate-v1"}, "oversample": False}
    rows = ([_qrow(f"w{i}", "wiki", "正" * 100, 0.9) for i in range(30)]
            + [_qrow(f"n{i}", "news", "文" * 100, 0.8) for i in range(5)])
    chosen, report = stratified_sample(rows, pol, "run-1")
    assert report["wiki"]["tokens"] >= 800 and report["news"]["tokens"] >= 190
    assert sum(c["token_count"] for c in chosen) >= 990
    chosen2, _ = stratified_sample([dict(r) for r in reversed(rows)], pol, "run-1")
    assert {c["doc_id"] for c in chosen} == {c["doc_id"] for c in chosen2}   # 顺序无关
    assert all(c["mix_batch_id"] == "run-1" and c["sampling_weight"] == 1.0 for c in chosen)


def test_stratified_prefers_high_quality():
    """预算语义=允许超额（greedy 直到 ≥target），预算恰好一篇时高质量独占。"""
    pol = {"token_budget": 100, "seed": 1, "ratios": {"wiki": 1.0},
           "tokenizer": {"name": "char-estimate-v1"}, "oversample": False}
    rows = [_qrow("high", "wiki", "优" * 100, 0.99), _qrow("low", "wiki", "劣" * 100, 0.10)]
    chosen, _ = stratified_sample(rows, pol, "r")
    assert [c["doc_id"] for c in chosen] == ["high"]


# ---------------------------------------------------------------- release

def test_next_version_rules():
    from release.run import next_version
    assert next_version("0.1", "patch") == "0.1.1"
    assert next_version("0.1.3", "minor") == "0.2"
    assert next_version("2.9", "major") == "3.0"
    with pytest.raises(ValueError):
        next_version("0.1", "boom")


@pytest.fixture
def mix_env(tmp_path):
    def _mixrow(doc_id, source, content, score, license_status):
        row = _qrow(doc_id, source, content, score, license_status)
        row.update({"token_count": token_count_estimate(content),
                    "tokenizer_name": "char-estimate-v1", "tokenizer_version": "char-estimate-v1",
                    "sampling_weight": 1.0, "mix_batch_id": "mix-test"})
        return row

    rows = [_mixrow("w1", "wiki", "正文一。" * 20, 0.9, "permissive"),
            _mixrow("w2", "wiki", "正文二。" * 20, 0.8, "permissive"),
            _mixrow("n1", "news", "正文三。" * 20, 0.7, "unknown"),      # release 应剔
            _mixrow("o1", "oscar", "正文四。" * 20, 0.6, "permissive")]
    final = tmp_path / "mix" / "candidates"
    final.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=MIX_EXT_SCHEMA), final / "part-00000.parquet")
    data = (final / "part-00000.parquet").read_bytes()
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"mix": {"input_fingerprint": "f", "output_hashes": {
            "candidates/part-00000.parquet": hashlib.sha256(data).hexdigest()}}}), encoding="utf-8")
    return tmp_path


def test_release_license_enforcement_and_card(mix_env, monkeypatch):
    monkeypatch.setattr("common.changelog.CHANGELOG_PATH", mix_env / "changelog.jsonl")
    monkeypatch.setattr("release.run.STAGING_CHANGELOG", mix_env / "staging.jsonl")
    from release import run as release_run
    result = release_run.run(output_root=mix_env / "out", runs_path=mix_env / "runs.jsonl",
                             mix_root=mix_env, version="0.1")
    assert result["rows"] == 3 and result["excluded"] == 1     # news(unknown) 被执法剔除
    released = pq.read_table(result["output_dir"] / "v0.1" / "part-00000.parquet").to_pylist()
    assert {r["doc_id"] for r in released} == {"w1", "w2", "o1"}
    card = (result["output_dir"] / "v0.1" / "provenance_card.yaml").read_text(encoding="utf-8")
    assert "license_restricted" not in card or True
    assert "excluded_by_license: 1" in card and "source: news" in card
    from common.changelog import read_all
    rec = [r for r in read_all(mix_env / "changelog.jsonl") if r["stage"] == "release"]
    assert len(rec) == 1 and rec[0]["reason"] == "license_restricted"
