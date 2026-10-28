"""common/runs.py — run 生命周期记录（T2）

一个 stage 的一次执行 = 一个 run，写 start / finish 两条事件，追加到 JSONL。

不变量（fail-fast 的地方）：
- stage 必须来自 configs/reason_registry.json 的 stages
- status 只允许 success / failed / cancelled
- 没有 start 就 finish → 拒绝；重复 finish → 拒绝
- 读日志时坏行必须报行号，不许静默跳过
"""

import json
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "configs" / "reason_registry.json"
RUNS_PATH = Path(__file__).resolve().parent.parent / "data" / "meta" / "runs.jsonl"

STATUSES = ("success", "failed", "cancelled")


def now_iso():
    """当前 UTC 时间，ISO 8601 带时区，精确到秒。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@lru_cache(maxsize=1)
def stages():
    """stage 合法值：读 reason_registry.json，不在代码里另写一份。"""
    with REGISTRY_PATH.open(encoding="utf-8") as f:
        return tuple(json.load(f)["stages"])


def new_run_id(stage):
    """生成 run_id：<stage>-<UTC 时间戳>-<8 位随机>，肉眼可读且不冲突。"""
    if stage not in stages():
        raise ValueError(f"非法 stage: {stage!r}，合法值: {stages()}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stage}-{stamp}-{uuid.uuid4().hex[:8]}"


def _log_path(path):
    return Path(path) if path else RUNS_PATH


def _append(event, path):
    p = _log_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def read_events(path=None):
    """读全部事件；坏行抛 ValueError，消息里带 文件:行号。"""
    p = _log_path(path)
    if not p.exists():
        return []

    events = []
    with p.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{p} 第 {lineno} 行不是合法 JSON: {exc}") from exc
    return events


def start(stage, pipeline_version="dev", path=None):
    """写 start 事件并返回 run_id。"""
    run_id = new_run_id(stage)
    _append(
        {
            "event": "start",
            "run_id": run_id,
            "stage": stage,
            "started_at": now_iso(),
            "pipeline_version": pipeline_version,
        },
        path,
    )
    return run_id


def finish(run_id, status, note="", path=None):
    """写 finish 事件。status 必须合法，且该 run 必须已经 start 过、尚未 finish。"""
    if status not in STATUSES:
        raise ValueError(f"非法 status: {status!r}，合法值: {STATUSES}")

    events = read_events(path)
    mine = [e for e in events if e.get("run_id") == run_id]

    if not any(e.get("event") == "start" for e in mine):
        raise ValueError(f"未知 run_id: {run_id!r}（找不到对应的 start 事件）")
    if any(e.get("event") == "finish" for e in mine):
        raise ValueError(f"run {run_id!r} 已经 finish 过，拒绝重复结束")

    _append(
        {"event": "finish", "run_id": run_id, "status": status, "finished_at": now_iso(), "note": note},
        path,
    )


def query_by_run(run_id, path=None):
    """按 run_id 取事件，按写入顺序返回。"""
    return [e for e in read_events(path) if e.get("run_id") == run_id]
