"""dedup 引擎与阶段测试（M5）"""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from common import ids
from common.stage import latest_outputs
from ingest.spine_schema import SPINE_PARQUET_SCHEMA

CLOCK = "2026-10-30T00:00:00+00:00"


def _row(doc_id, source, content, **over):
    from common.textstats import text_stats
    s = text_stats(content)
    row = {"doc_id": doc_id, "schema_version": "t", "source": source, "source_id": doc_id,
           "url": None, "canonical_url": None, "content_hash": ids.content_hash(content),
           "content": content, "content_format": "plain_text", "raw_uri": None,
           "original_encoding": None, "language": "zh", "char_count": s["char_count"],
           "cjk_char_count": s["cjk_char_count"], "utf8_byte_count": s["utf8_byte_count"],
           "source_timestamp": None, "timestamp_source": "none", "first_seen_at": CLOCK,
           "genre": "wiki", "license": "CC0", "license_status": "permissive",
           "commercial_use": "allowed",
           "source_metadata": {"page_title": None, "publisher": None, "section": None}}
    row.update(over)
    return row


# ---------------------------------------------------------------- 引擎

def test_exact_dup_collapses_across_format():
    from dedup.engine import dedup_pass
    a = _row("a", "oscar", "完全相同的正文内容，用于精确去重测试。")
    b = _row("b", "wiki", "完全相同的正文内容，用于精确去重测试。")   # 跨源同文
    c = _row("c", "wiki", "完全相同的正文内容，用于精确去重测试。　")  # 多一个全角空格→规范化后同哈希
    d = _row("d", "wiki", "完全不同的另一篇正文，不参与去重。")
    for r in (a, b, c, d):
        r["normalized_hash"] = ids.normalized_content_hash(r["content"])
    result = dedup_pass([a, b, c, d])
    kept_ids = {r["doc_id"] for r in result["keep"]}
    assert kept_ids == {"a", "d"} or kept_ids == {"b", "d"} or kept_ids == {"c", "d"}  # 代表由策略定
    exact = [e for e in result["removed"] if e["reason"] == "exact_dup"]
    assert len(exact) == 2 and all(e["similarity"] == 1.0 for e in exact)
    assert result["cross_source"].get(("oscar", "wiki"), 0) >= 1  # 跨源共现被记账


def test_canonical_prefers_trusted_source():
    from dedup.engine import dedup_pass
    a = _row("a", "oscar", "可信度排序测试正文，内容一致。")
    b = _row("b", "wiki", "可信度排序测试正文，内容一致。")
    for r in (a, b):
        r["normalized_hash"] = ids.normalized_content_hash(r["content"])
    result = dedup_pass([a, b])
    assert [r["doc_id"] for r in result["keep"]] == ["b"]       # wiki 信任序高于 oscar
    assert result["removed"][0]["ref_doc_id"] == "b"


def test_near_dup_with_paraphrase():
    from dedup.engine import dedup_pass
    base = ("这是一段用于近似去重测试的中文正文，包含足够多的字符以形成稳定的 shingle 特征。"
            "正文继续延展以保证编辑距离的相对占比足够小，从而使 Jaccard 相似度越过阈值。") * 3
    near = base + "结尾略有差异。"  # 长文小改 → Jaccard ≈ 0.9+
    a = _row("a", "wiki", base)
    b = _row("b", "wiki", near)
    for r in (a, b):
        r["normalized_hash"] = ids.normalized_content_hash(r["content"])
    result = dedup_pass([a, b])
    reasons = {e["reason"] for e in result["removed"]}
    assert "near_dup" in reasons
    near_entry = next(e for e in result["removed"] if e["reason"] == "near_dup")
    assert near_entry["similarity"] >= 0.8 and near_entry["ref_doc_id"] == "b"  # canonical=内容更长者(near 比原文多后缀)


def test_engine_is_deterministic():
    from dedup.engine import dedup_pass
    rows = [_row(f"d{i}", "wiki", f"第{i}篇正文内容，各不相同，长度适当。" * 2) for i in range(20)]
    for r in rows:
        r["normalized_hash"] = ids.normalized_content_hash(r["content"])
    r1 = dedup_pass(list(rows))
    r2 = dedup_pass(list(reversed(rows)))  # 输入顺序无关（决策确定；输出序随输入序是文档化行为）
    assert {x["doc_id"] for x in r1["keep"]} == {x["doc_id"] for x in r2["keep"]}
    assert sorted(map(lambda e: e["row"]["doc_id"], r1["removed"])) == \
           sorted(map(lambda e: e["row"]["doc_id"], r2["removed"]))


# ---------------------------------------------------------------- 阶段（合同 T1 简版）

@pytest.fixture
def clean_env(tmp_path):
    rows = [
        _row("w1", "wiki", "维基正文甲，内容完整充实。"),
        _row("w2", "wiki", "维基正文甲，内容完整充实。"),            # exact 同文
        _row("n1", "news", "维基正文甲，内容完整充实。"),            # 跨源 exact
        _row("o1", "oscar", "爬虫正文乙，与别篇完全不同。"),
    ]
    final = tmp_path / "clean" / "source=x"
    final.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=SPINE_PARQUET_SCHEMA), final / "part-00000.parquet")
    import hashlib
    data = (final / "part-00000.parquet").read_bytes()
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"clean": {"input_fingerprint": "f", "output_hashes": {
            "source=x/part-00000.parquet": hashlib.sha256(data).hexdigest()}}}), encoding="utf-8")
    return tmp_path


def test_dedup_stage_contract(clean_env, tmp_path, monkeypatch):
    monkeypatch.setattr("common.changelog.CHANGELOG_PATH", clean_env / "changelog.jsonl")
    monkeypatch.setattr("dedup.run.STAGING_CHANGELOG", clean_env / "staging.jsonl")
    from dedup import run as dedup_run
    kwargs = dict(output_root=clean_env / "out", runs_path=clean_env / "runs.jsonl",
                  clean_root=clean_env)
    first = dedup_run.run(**kwargs)
    assert first["cached"] is False
    assert first["rows"] == 2 and first["report"]["removed_exact"] == 2
    assert first["report"]["cross_source_overlap"].get("news|wiki") == 1
    second = dedup_run.run(**kwargs)
    assert second["cached"] is True
    # changelog 三条 exact_dup（w2/n1 对代表）且 dedup 阶段归因正确
    from common.changelog import read_all
    recs = [r for r in read_all(clean_env / "changelog.jsonl") if r["stage"] == "dedup"]
    assert len(recs) == 2 and {r["reason"] for r in recs} == {"exact_dup"}
