"""quality 引擎与阶段测试（M6）——含方案纪律第 2 条要求的变异测试（坏数据必须变红）"""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from common import ids
from common.stage import latest_outputs
from ingest.spine_schema import SPINE_PARQUET_SCHEMA

CLOCK = "2026-10-30T00:00:00+00:00"


def _row(content, genre="wiki", **over):
    from common.textstats import text_stats
    s = text_stats(content)
    row = {"doc_id": ids.spine_doc_id("wiki", content[:20]), "schema_version": "t",
           "source": "wiki", "source_id": "1", "url": None, "canonical_url": None,
           "content_hash": ids.content_hash(content), "content": content,
           "content_format": "plain_text", "raw_uri": None, "original_encoding": None,
           "language": "zh", "char_count": s["char_count"], "cjk_char_count": s["cjk_char_count"],
           "utf8_byte_count": s["utf8_byte_count"], "source_timestamp": None,
           "timestamp_source": "none", "first_seen_at": CLOCK, "genre": genre,
           "license": "CC0", "license_status": "permissive", "commercial_use": "allowed",
           "source_metadata": {"page_title": None, "publisher": None, "section": None}}
    row.update(over)
    return row


GOOD = ("这是一篇结构完整的百科式正文。第一段陈述事实与定义。\n"
        "第二段展开背景与沿革，内容充实有条理。\n"
        "第三段给出结论并附参考指向。") * 5


# ---------------------------------------------------------------- 五维

def test_good_row_passes_all_dims():
    from quality.engine import score_row
    scores = score_row(_row(GOOD))
    assert scores["quality_completeness"] == 1.0
    assert scores["quality_length"] == 1.0
    assert scores["quality_score"] > 0.7


def test_completeness_ignores_header_lines():
    """结构行豁免：标题/列表混排的正常文档不该被 completeness 误杀（M6 真实数据回归）。"""
    from quality.engine import score_completeness
    wiki_like = _row("《孙膑兵法》是中国古代著名的兵书之一，作者是战国时齐国人孙膑。\n"
                     "题解\n"
                     "《孙膑兵法》最早见于《汉书·艺文志》，东汉以后就已经失传，历史学家钱穆曾考证。\n"
                     "评价\n"
                     "该书进一步发展了军事思想，是一部内容丰富的著作。\n"
                     "版本\n"
                     "1972年山东临沂银雀山汉墓出土竹简本。\n"
                     "孙膑\n兵书\nS")
    assert score_completeness(wiki_like) >= 0.75   # 4 句式行中 3-4 个以标点结尾


def test_length_band_by_genre():
    from quality.engine import score_length
    news_300 = _row("正" * 300, genre="news")
    wiki_300 = _row("正" * 300, genre="wiki")     # wiki 带下限 200 → 300 在带内
    assert score_length(wiki_300) == 1.0
    assert score_length(_row("正" * 10, genre="wiki")) < 0.5


def test_contamination_detection():
    from quality.engine import build_ngram_index, score_row
    bench = Path("data/benchmarks/_test_ceval.jsonl")
    bench.parent.mkdir(parents=True, exist_ok=True)
    leaked = "下列关于递归函数的说法中正确的是下列关于递归函数的说法中正确的是" * 3
    bench.write_text(json.dumps({"text": leaked}, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        index = build_ngram_index([bench])
        assert len(index) > 0
        polluted = _row(GOOD + leaked)
        scores = score_row(polluted, contamination_index=index)
        assert scores["quality_non_pollution"] == 0.0      # 评测集原文 = 污染重罪
        clean = score_row(_row(GOOD), contamination_index=index)
        assert clean["quality_non_pollution"] == 1.0
    finally:
        bench.unlink()


def test_empty_index_is_skipped_not_fake():
    from quality.engine import build_ngram_index, score_row
    empty = build_ngram_index([Path("data/benchmarks/不存在.jsonl")])
    assert empty == set()
    scores = score_row(_row(GOOD), contamination_index=None)
    assert scores["quality_non_pollution"] == 1.0 and scores["pollution_skipped"] is True


# ---------------------------------------------------------------- 门禁 + 变异测试（纪律 2）

def test_gate_variants_mutation_matrix():
    """变异测试：三类坏数据必须被门禁拦住（方案纪律 2：坏数据必须变红）。"""
    from quality.engine import gate, score_row

    # 变异 1：截断文（段落全无标点结尾）→ completeness 崩
    truncated = _row("这是被截断的正文第一段没有结尾标点\n第二段同样戛然而止\n第三段没了" * 5)
    v1 = gate(score_row(truncated))
    assert v1[0] in ("remove", "hold"), f"截断文未拦截: {v1}"

    # 变异 2：超短文 → length 维崩 → remove
    stub = _row("太短。")
    assert gate(score_row(stub))[0] == "remove"

    # 变异 3：好文必须放行
    assert gate(score_row(_row(GOOD)))[0] == "pass"


# ---------------------------------------------------------------- 阶段合同

@pytest.fixture
def dedup_env(tmp_path):
    rows = [_row(GOOD), _row(GOOD[: len(GOOD) // 2] + "。", genre="news")]
    final = tmp_path / "dedup" / "source=x"
    final.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=SPINE_PARQUET_SCHEMA), final / "part-00000.parquet")
    import hashlib
    data = (final / "part-00000.parquet").read_bytes()
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"dedup": {"input_fingerprint": "f", "output_hashes": {
            "source=x/part-00000.parquet": hashlib.sha256(data).hexdigest()}}}), encoding="utf-8")
    return tmp_path


def test_quality_stage_contract(dedup_env, monkeypatch):
    monkeypatch.setattr("common.changelog.CHANGELOG_PATH", dedup_env / "changelog.jsonl")
    monkeypatch.setattr("quality.run.STAGING_CHANGELOG", dedup_env / "staging.jsonl")
    from quality import run as quality_run
    kwargs = dict(output_root=dedup_env / "out", runs_path=dedup_env / "runs.jsonl",
                  dedup_root=dedup_env)
    first = quality_run.run(**kwargs)
    assert first["cached"] is False
    assert first["report"]["total"] == 2
    part = first["output_dir"] / "source=wiki" / "part-00000.parquet"
    cols = pq.ParquetFile(part).schema_arrow.names
    assert "quality_score" in cols and "quality_run_id" in cols
    assert quality_run.run(**kwargs)["cached"] is True
