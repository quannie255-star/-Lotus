"""跨模块集成测试（必修 2）：run_stage → runs 审计链 → changelog 归属 → 重跑零新增。

这一条就是「审计链对上账」的可执行版本：
一次 stage 运行必须同时留下 start/finish 对，且它产生的每条变更记录都带着这个 run_id；
重跑命中缓存时，变更日志零新增、runs 里标 cached。
"""

from common import changelog, runs
from common.idempotent import run_stage


def test_audit_chain_is_consistent(tmp_path):
    src = tmp_path / "in.txt"
    src.write_text("这是一段太短的文本", encoding="utf-8")
    runs_log = tmp_path / "runs.jsonl"
    change_log = tmp_path / "changelog.jsonl"
    executed = []

    def transform(input_paths, work_dir, run_id):
        executed.append(run_id)
        text = input_paths[0].read_text(encoding="utf-8")
        out = work_dir / "out.txt"
        out.write_text(text, encoding="utf-8")

        changelog.append_change(
            {
                "run_id": run_id,
                "doc_id": "doc-1",
                "source": "wiki",
                "stage": "clean",
                "action": "remove",
                "reason": "too_short",
                "changed_by": "integration-test",
                "old_hash": "aaa",
            },
            path=change_log,
            dedupe=True,
        )
        return [out]

    # 第一次：真执行
    first = run_stage("clean", transform, [src], tmp_path / "out", runs_path=runs_log)
    assert first["cached"] is False

    events = runs.read_events(runs_log)
    assert [e["event"] for e in events] == ["start", "finish"]
    assert {e["run_id"] for e in events} == {first["run_id"]}
    assert events[1]["status"] == "success"
    assert events[1]["note"] == "executed"

    records = changelog.read_all(change_log)
    assert len(records) == 1
    assert records[0]["run_id"] == first["run_id"]

    # 第二次：命中缓存，零处理、零新增
    second = run_stage("clean", transform, [src], tmp_path / "out", runs_path=runs_log)
    assert second["cached"] is True
    assert len(changelog.read_all(change_log)) == 1  # changelog 零新增
    assert executed == [first["run_id"]]  # transform 只跑过一次

    events = runs.read_events(runs_log)
    assert len(events) == 4  # 两次 run 各一对 start/finish
    assert events[-1]["note"] == "cached"
    assert events[-1]["run_id"] == second["run_id"]
