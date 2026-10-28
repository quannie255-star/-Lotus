"""T4 测试：重跑零处理、输出字节一致、缓存验货、审计链留痕。"""

import hashlib

import pytest

from common import runs
from common.idempotent import fingerprint, load_manifest, run_stage


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _transform(calls):
    def transform(input_paths, work_dir, run_id):
        calls.append(run_id)
        text = input_paths[0].read_text(encoding="utf-8")
        out = work_dir / "out.txt"
        out.write_text(text.upper(), encoding="utf-8")
        return [out]

    return transform


def test_second_run_is_cached(tmp_path):
    calls = []
    src = tmp_path / "in.txt"
    src.write_text("hello", encoding="utf-8")
    log = tmp_path / "runs.jsonl"

    first = run_stage("clean", _transform(calls), [src], tmp_path / "out", runs_path=log)
    second = run_stage("clean", _transform(calls), [src], tmp_path / "out", runs_path=log)

    assert first["cached"] is False
    assert second["cached"] is True
    assert len(calls) == 1  # transform 只执行过一次


def test_each_run_leaves_start_finish_pair(tmp_path):
    calls = []
    src = tmp_path / "in.txt"
    src.write_text("hello", encoding="utf-8")
    log = tmp_path / "runs.jsonl"

    first = run_stage("clean", _transform(calls), [src], tmp_path / "out", runs_path=log)
    second = run_stage("clean", _transform(calls), [src], tmp_path / "out", runs_path=log)

    events = runs.read_events(log)
    assert [e["event"] for e in events] == ["start", "finish", "start", "finish"]
    assert {e["run_id"] for e in events} == {first["run_id"], second["run_id"]}
    assert [e["note"] for e in events if e["event"] == "finish"] == ["executed", "cached"]


def test_failed_run_is_recorded(tmp_path):
    src = tmp_path / "in.txt"
    src.write_text("x", encoding="utf-8")
    log = tmp_path / "runs.jsonl"

    def boom(input_paths, work_dir, run_id):
        raise RuntimeError("transform 炸了")

    with pytest.raises(RuntimeError):
        run_stage("clean", boom, [src], tmp_path / "out", runs_path=log)

    finish_events = [e for e in runs.read_events(log) if e["event"] == "finish"]
    assert len(finish_events) == 1
    assert finish_events[0]["status"] == "failed"
    assert "RuntimeError" in finish_events[0]["note"]


def test_force_recompute_is_byte_identical(tmp_path):
    calls = []
    src = tmp_path / "in.txt"
    src.write_text("hello", encoding="utf-8")
    log = tmp_path / "runs.jsonl"

    first = run_stage("clean", _transform(calls), [src], tmp_path / "out", runs_path=log)
    second = run_stage("clean", _transform(calls), [src], tmp_path / "out", force=True, runs_path=log)

    assert _sha(first["output_dir"] / "out.txt") == _sha(second["output_dir"] / "out.txt")


def test_input_change_triggers_recompute(tmp_path):
    calls = []
    src = tmp_path / "in.txt"
    src.write_text("hello", encoding="utf-8")
    log = tmp_path / "runs.jsonl"

    run_stage("clean", _transform(calls), [src], tmp_path / "out", runs_path=log)
    src.write_text("hello world", encoding="utf-8")
    result = run_stage("clean", _transform(calls), [src], tmp_path / "out", runs_path=log)

    assert result["cached"] is False
    assert len(calls) == 2


def test_tampered_output_invalidates_cache(tmp_path):
    """必修 1：产物被改过就不能命中缓存，必须重算。"""
    calls = []
    src = tmp_path / "in.txt"
    src.write_text("hello", encoding="utf-8")
    log = tmp_path / "runs.jsonl"

    first = run_stage("clean", _transform(calls), [src], tmp_path / "out", runs_path=log)
    (first["output_dir"] / "out.txt").write_text("被手改了", encoding="utf-8")

    second = run_stage("clean", _transform(calls), [src], tmp_path / "out", runs_path=log)

    assert second["cached"] is False
    assert len(calls) == 2
    assert (second["output_dir"] / "out.txt").read_text(encoding="utf-8") == "HELLO"


def test_manifest_written(tmp_path):
    src = tmp_path / "in.txt"
    src.write_text("x", encoding="utf-8")

    run_stage("clean", _transform([]), [src], tmp_path / "out", runs_path=tmp_path / "runs.jsonl")
    manifest = load_manifest(tmp_path / "out" / "manifest.json")

    assert "clean" in manifest
    assert manifest["clean"]["input_fingerprint"] == fingerprint([src])


def test_missing_input_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_stage("clean", _transform([]), [tmp_path / "ghost.txt"], tmp_path / "out", runs_path=tmp_path / "runs.jsonl")
