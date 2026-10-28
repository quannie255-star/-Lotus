"""common/changelog.py — 变更日志（T2）

每条 remove / modify / redact / quarantine 写一行 JSON，结构由
schema/removal_log_schema.json 约束；写入前校验，不合法拒写。

跨字段规则写成"规则表"而不是 if 堆叠：加一条规则只加一个 dict。
坏行报错带行号：一万行日志里没有行号等于没报错。
"""

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "removal_log_schema.json"
REGISTRY_PATH = Path(__file__).resolve().parent.parent / "configs" / "reason_registry.json"
CHANGELOG_PATH = Path(__file__).resolve().parent.parent / "data" / "meta" / "changelog.jsonl"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@lru_cache(maxsize=1)
def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def reason_registry():
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


# 跨字段规则表：when 命中时必须满足 require，否则拒写
CROSS_FIELD_RULES = (
    {
        "when": lambda r: r["action"] == "quarantine",
        "require": lambda r: r.get("review_status") == "pending_review",
        "message": "action=quarantine 时 review_status 必须是 pending_review",
    },
    {
        "when": lambda r: r.get("review_status") in ("approved", "rejected"),
        "require": lambda r: bool(r.get("reviewer")),
        "message": "review_status 为 approved/rejected 时 reviewer 不能为空",
    },
    {
        "when": lambda r: r["action"] == "redact",
        "require": lambda r: bool(r.get("pii_types")),
        "message": "action=redact 时 pii_types 不能为空",
    },
    {
        "when": lambda r: r["action"] in ("remove", "modify"),
        "require": lambda r: r.get("old_hash") is not None,
        "message": "action=remove/modify 时必须提供 old_hash，否则无法审计改了什么",
    },
)


def _check_reason(record):
    """reason 必须登记在 reason_registry，且其 stage 与记录的 stage 一致。"""
    reasons = reason_registry()["reasons"]
    reason = record["reason"]
    if reason not in reasons:
        raise ValueError(f"未登记的 reason: {reason!r}")
    expected_stage = reasons[reason]["stage"]
    if record["stage"] != expected_stage:
        raise ValueError(f"reason {reason!r} 属于 stage {expected_stage!r}，与记录的 {record['stage']!r} 不符")


def validate(record):
    """schema 校验 + 跨字段规则 + reason 归属校验。"""
    if not isinstance(record, dict):
        raise ValueError("记录必须是 dict")

    try:
        jsonschema.validate(record, schema())
    except jsonschema.ValidationError as exc:
        path = ".".join(str(p) for p in exc.absolute_path) or "<root>"
        raise ValueError(f"字段 {path} 不合法: {exc.message}") from exc

    _check_reason(record)

    for rule in CROSS_FIELD_RULES:
        if rule["when"](record) and not rule["require"](record):
            raise ValueError(rule["message"])


def _parse_line(line, path, lineno):
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} 第 {lineno} 行不是合法 JSON: {exc}") from exc


def append_change(record, path=None, dedupe=False):
    """校验后追加一行，返回写入的行号（从 1 开始）。

    dedupe=True 时：若同一 (doc_id, stage, action, reason, old_hash) 已存在，
    不重复写，直接返回已有行号——重跑不会把日志喂胖。
    """
    p = Path(path) if path else CHANGELOG_PATH

    record = dict(record)
    record.setdefault("changed_at", now_iso())
    record.setdefault("review_status", "auto")
    validate(record)

    key = (record["doc_id"], record["stage"], record["action"], record["reason"], record.get("old_hash"))

    lineno = 0
    if p.exists():
        with p.open(encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if not line.strip():
                    continue
                old = _parse_line(line, p, lineno)
                if dedupe and (
                    old.get("doc_id"),
                    old.get("stage"),
                    old.get("action"),
                    old.get("reason"),
                    old.get("old_hash"),
                ) == key:
                    return lineno

    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return lineno + 1


def read_all(path=None):
    """读全部变更记录；坏行报错带行号。"""
    p = Path(path) if path else CHANGELOG_PATH
    if not p.exists():
        return []

    records = []
    with p.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            records.append(_parse_line(line, p, lineno))
    return records


def query_by_doc(doc_id, path=None):
    """按 doc_id 取该文档的全部变更记录（review 意见补的点）。"""
    return [r for r in read_all(path) if r.get("doc_id") == doc_id]
