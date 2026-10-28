"""T2 测试：schema 校验、跨字段规则、reason 归属、坏行行号、去重。"""

import pytest

from common import changelog


def make_record(**overrides):
    record = {
        "run_id": "run-1",
        "doc_id": "doc-1",
        "source": "wiki",
        "stage": "clean",
        "action": "remove",
        "reason": "too_short",
        "changed_by": "test",
        "old_hash": "aaa",
    }
    record.update(overrides)
    return record


def test_append_and_query(tmp_path):
    log = tmp_path / "changelog.jsonl"
    line = changelog.append_change(make_record(), path=log)

    assert line == 1
    hits = changelog.query_by_doc("doc-1", log)
    assert len(hits) == 1
    assert hits[0]["changed_at"]  # 自动补时间
    assert hits[0]["review_status"] == "auto"


def test_missing_required_field(tmp_path):
    record = make_record()
    del record["doc_id"]
    with pytest.raises(ValueError, match="doc_id"):
        changelog.append_change(record, path=tmp_path / "c.jsonl")


def test_invalid_action_enum(tmp_path):
    with pytest.raises(ValueError):
        changelog.append_change(make_record(action="delete"), path=tmp_path / "c.jsonl")


def test_quarantine_requires_pending_review(tmp_path):
    record = make_record(
        action="quarantine",
        stage="quality",
        reason="quarantine_borderline",
    )
    with pytest.raises(ValueError, match="pending_review"):
        changelog.append_change(record, path=tmp_path / "c.jsonl")


def test_redact_requires_pii_types(tmp_path):
    record = make_record(
        action="redact",
        reason="pii_redacted",
        old_hash="bbb",
    )
    with pytest.raises(ValueError, match="pii_types"):
        changelog.append_change(record, path=tmp_path / "c.jsonl")


def test_reason_must_match_stage(tmp_path):
    # near_dup 属于 dedup，却冒充 clean
    with pytest.raises(ValueError, match="stage"):
        changelog.append_change(make_record(reason="near_dup"), path=tmp_path / "c.jsonl")


def test_bad_line_reports_lineno(tmp_path):
    log = tmp_path / "changelog.jsonl"
    # 第二行不是合法 JSON；read_all 必须报错并带行号，不许静默跳过
    log.write_text('{"doc_id": "x"}\n{oops}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="第 2 行"):
        changelog.read_all(log)


def test_dedupe_on_rerun(tmp_path):
    log = tmp_path / "changelog.jsonl"
    changelog.append_change(make_record(), path=log, dedupe=True)
    changelog.append_change(make_record(), path=log, dedupe=True)

    assert len(changelog.read_all(log)) == 1
