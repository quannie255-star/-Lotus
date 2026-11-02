"""metrics 指标卡测试（Tier 0）"""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from common import ids
from ingest.spine_schema import SPINE_PARQUET_SCHEMA
from metrics.card import compare_cards, compute_card

CLOCK = "2026-10-31T00:00:00+00:00"


def _row(content, quality=None):
    from common.textstats import text_stats
    s = text_stats(content)
    row = {"doc_id": ids.spine_doc_id("wiki", content[:12]), "schema_version": "t",
           "source": "wiki", "source_id": "1", "url": None, "canonical_url": None,
           "content_hash": ids.content_hash(content), "content": content,
           "content_format": "plain_text", "raw_uri": None, "original_encoding": None,
           "language": "zh", "char_count": s["char_count"], "cjk_char_count": s["cjk_char_count"],
           "utf8_byte_count": s["utf8_byte_count"], "source_timestamp": None,
           "timestamp_source": "none", "first_seen_at": CLOCK, "genre": "wiki",
           "license": "CC0", "license_status": "permissive", "commercial_use": "allowed",
           "source_metadata": {"page_title": None, "publisher": None, "section": None}}
    if quality is not None:
        row["quality_score"] = quality
    return row


def _write(parts_dir: Path, rows, name="part-00000.parquet"):
    parts_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=SPINE_PARQUET_SCHEMA), parts_dir / name)
    return parts_dir / name


def test_card_metrics_and_determinism(tmp_path):
    dup = "完全相同的一段正文内容，用来制造精确重复。"
    rows = ([_row(f"第{i}篇正常正文，内容完整并以句号收尾。") for i in range(10)]
            + [_row(dup), _row(dup + "　")])   # 规范化后同哈希的重复对
    part = _write(tmp_path / "raw", rows)
    c1 = compute_card([part], "t")
    c2 = compute_card([part], "t")
    assert c1 == c2                                    # 确定性
    assert c1["n_docs"] == 12
    assert c1["exact_dup_rate"] == pytest.approx(1 / 12, abs=1e-4)  # 卡片值舍入到 5 位
    assert 0 < c1["format_completeness"] <= 1
    assert c1["semantic_diversity"] is None            # 空读数不假装有数
    assert c1["sampled_rows"] == 12


def test_compare_cards_direction_verdict():
    before = {"label": "v0", "n_docs": 100, "exact_dup_rate": 0.2, "format_completeness": 0.5,
              "info_density_proxy": 0.6, "internal_redundancy": 0.4, "language_consistency": 0.8}
    after = {"label": "v1", "n_docs": 80, "exact_dup_rate": 0.05, "format_completeness": 0.7,
             "info_density_proxy": 0.8, "internal_redundancy": 0.2, "language_consistency": 0.9}
    comp = compare_cards(before, after)
    assert comp["metrics"]["exact_dup_rate"]["verdict"] == "改善"    # 重复率降=好
    assert comp["metrics"]["format_completeness"]["verdict"] == "改善"
    assert comp["metrics"]["n_docs"]["verdict"] == "规模"


def test_card_rejects_empty():
    with pytest.raises(ValueError):
        compute_card([], "empty")


def test_quality_score_present_when_column_exists(tmp_path):
    # quality_score 不在 spine schema 里——写入时用扩展 schema 保留该列
    import pyarrow as pa
    ext = pa.schema(list(SPINE_PARQUET_SCHEMA) + [pa.field("quality_score", pa.float64())])
    (tmp_path / "q").mkdir(parents=True)
    part = tmp_path / "q" / "part-00000.parquet"
    pq.write_table(pa.Table.from_pylist([_row("正文内容。", quality=0.9)], schema=ext), part)
    card = compute_card([part], "q")
    assert card["mean_quality_score"] == 0.9
