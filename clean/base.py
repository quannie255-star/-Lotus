"""clean/base.py — 规则框架：规则是数据不是代码分支（M4）（DEVLOG M4-D2）

一条规则 = (name, fn)。fn(row, cfg) -> 判定：
  ("keep",)                                          保留，继续下一条
  ("drop", reason)                                   删除并短路（changelog action=remove）
  ("transform", new_row, reason, action, meta)       改写并继续；meta 是审计载荷
    （pii 的 meta={old_hash,new_hash,pii_types,before/after}）——元数据走判定不走行，
     否则会污染封闭 schema 的输出（M4 实现期第一版就犯了这个错，重构于当日）

框架职责：装配（policy.rules_order）、changelog 暂存（StagedChangeLog）、
每规则×每源计数（stats 侧信道，看板数据源）。规则本体是无状态纯函数。
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "configs" / "clean_policy.json"


@lru_cache(maxsize=1)
def policy() -> dict:
    """clean_policy 全文。lru_cache 进程内只读一次；配置变更由快照指纹强制重算。"""
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def apply_rules(row: dict, rules: list) -> tuple:
    """一行过规则链。返回 (final_row|None, changes)；None=被删。

    changes 元素: {"rule", "reason", "action", "meta", "row_before"}——row_before 供
    changelog 记 old_hash（改写后行里已是新哈希）。
    幂等: 纯函数。
    """
    changes = []
    for name, fn in rules:
        verdict = fn(row, policy()[name])
        kind = verdict[0]
        if kind == "keep":
            continue
        if kind == "drop":
            changes.append({"rule": name, "reason": verdict[1], "action": "remove",
                            "meta": {}, "row_before": row})
            return None, changes
        if kind == "transform":
            _, new_row, reason, action, meta = verdict
            changes.append({"rule": name, "reason": reason, "action": action,
                            "meta": meta or {}, "row_before": row})
            row = new_row
            continue
        raise ValueError(f"规则 {name} 返回非法判定 {verdict!r}")
    return row, changes


def load_rules() -> list:
    """按 policy.rules_order 装配。未登记规则名 = 配置漂移，fail-fast。"""
    from clean import rules as rule_fns

    loaded = []
    for name in policy()["rules_order"]:
        fn = getattr(rule_fns, name, None)
        if fn is None:
            raise ValueError(f"clean_policy.rules_order 引用了不存在的规则: {name}")
        loaded.append((name, fn))
    return loaded


def iter_clean(docs: Iterator[dict], rules: list, slog, changed_by: str = "clean") -> tuple:
    """流式清洗。返回 (保留行生成器, stats)——stats={source: {rule: {removed, transformed}}}。

    changelog 记录不携带 run_id（由 StagedChangeLog.commit 注入真实值——行内占位身份
    会因 setdefault 不覆盖而永久污染账本，M4 实现期自查发现）。url_norm 的 transform
    是派生字段回填，显式豁免不记（M4-D3）。所有记录先入暂存 part，成功后才 commit。
    """
    stats = {}

    def _bump(source, rule, key):
        stats.setdefault(source, {}).setdefault(rule, {"removed": 0, "transformed": 0})
        stats[source][rule][key] += 1

    def generate():
        for row in docs:
            source = row["source"]
            final, changes = apply_rules(row, rules)
            for change in changes:
                rule, reason, action = change["rule"], change["reason"], change["action"]
                meta, before = change["meta"], change["row_before"]
                _bump(source, rule, "removed" if action == "remove" else "transformed")
                if rule == "url_norm":
                    continue
                record = {
                    "doc_id": before["doc_id"],
                    "source": source,
                    "stage": "clean",
                    "action": action,
                    "reason": reason,
                    "changed_by": f"{changed_by}:{rule}",
                    "old_hash": meta.get("old_hash", before.get("content_hash")),
                }
                if action == "redact":
                    record.update({
                        "new_hash": meta.get("new_hash"),
                        "pii_types": meta.get("pii_types", []),
                        "before_char_count": meta.get("before_chars"),
                        "after_char_count": meta.get("after_chars"),
                        "review_status": "auto",
                    })
                slog.log(record)
            if final is not None:
                yield final

    return generate(), stats
