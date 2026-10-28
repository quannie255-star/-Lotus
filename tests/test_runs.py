"""T2 测试：start/finish 事件对、枚举校验、坏行报行号。"""

import pytest

from common import runs


def test_start_finish_roundtrip(tmp_path):
    log = tmp_path / "runs.jsonl"

    run_id = runs.start("ingest", pipeline_version="test", path=log)
    runs.finish(run_id, "success", note="ok", path=log)

    events = runs.read_events(log)
    assert [e["event"] for e in events] == ["start", "finish"]
    assert runs.query_by_run(run_id, log)[-1]["status"] == "success"


def test_missing_log_reads_empty(tmp_path):
    assert runs.read_events(tmp_path / "nope.jsonl") == []


def test_invalid_stage_rejected(tmp_path):
    with pytest.raises(ValueError, match="非法 stage"):
        runs.start("nope", path=tmp_path / "runs.jsonl")


def test_invalid_status_rejected(tmp_path):
    log = tmp_path / "runs.jsonl"
    run_id = runs.start("clean", path=log)
    with pytest.raises(ValueError, match="非法 status"):
        runs.finish(run_id, "done", path=log)


def test_finish_without_start_rejected(tmp_path):
    with pytest.raises(ValueError, match="未知 run_id"):
        runs.finish("ghost-run", "success", path=tmp_path / "runs.jsonl")


def test_double_finish_rejected(tmp_path):
    log = tmp_path / "runs.jsonl"
    run_id = runs.start("clean", path=log)
    runs.finish(run_id, "failed", path=log)
    with pytest.raises(ValueError, match="已经 finish"):
        runs.finish(run_id, "success", path=log)


def test_bad_line_reports_lineno(tmp_path):
    log = tmp_path / "runs.jsonl"
    log.write_text('{"event": "start"}\n{oops}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="第 2 行"):
        runs.read_events(log)
