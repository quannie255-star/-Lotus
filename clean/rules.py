"""clean/rules.py — 规则族（M4）。每条规则 = 无状态纯函数 fn(row, cfg) -> 判定。

判定协议见 clean/base.py 模块头。阈值全部来自 configs/clean_policy.json（本文件零字面量阈值）。
reason 码全部登记在 configs/reason_registry.json（元测试守卫）。
"""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from clean import pii
from common.textstats import cjk_char_count

_URL_RE = re.compile(r"https?://\S+")


# ------------------------------------------------------------------ remove 规则

def too_short(row, cfg):
    """cjk_char_count < min_cjk_chars → drop(too_short)。"""
    if row["cjk_char_count"] < cfg["min_cjk_chars"]:
        return ("drop", cfg["reason"])
    return ("keep",)


def lang_ratio(row, cfg):
    """汉字占码点比 < min_han_ratio → drop(lang_mismatch)。

    v1 检测器 = 确定性脚本比率（cjk_char_count / char_count）——zh 管道下稳健可解释；
    fasttext 升级位 N4（换检测器只改本函数与配置，框架不动）。
    """
    ratio = (row["cjk_char_count"] / row["char_count"]) if row["char_count"] else 0.0
    if ratio < cfg["min_han_ratio"]:
        return ("drop", cfg["reason"])
    return ("keep",)


def mojibake(row, cfg):
    """U+FFFD 替换符密度超 max_fffd_per_kchar → drop(mojibake)。"""
    text = row["content"]
    fffd = text.count("\ufffd")
    per_k = (fffd * 1000 / row["char_count"]) if row["char_count"] else 0.0
    if per_k > cfg["max_fffd_per_kchar"]:
        return ("drop", cfg["reason"])
    return ("keep",)


def boilerplate(row, cfg):
    """链接密度与短行占比双指标 → drop(boilerplate_heavy)。任一超限即删（保守取向）。"""
    text, total = row["content"], row["char_count"]
    if total:
        link_chars = sum(len(m.group(0)) for m in _URL_RE.finditer(text))
        link_density = link_chars / total
        if link_density > cfg["max_link_density"]:
            return ("drop", cfg["reason"])
        lines = [l for l in text.splitlines() if l.strip()]
        if lines:
            short = sum(1 for l in lines if len(l.strip()) < 10)
            if (short / len(lines)) > cfg["max_short_line_ratio"]:
                return ("drop", cfg["reason"])
    return ("keep",)


# ------------------------------------------------------------------ transform 规则

def pii_redact(row, cfg):
    """命中 PII → 掩码改写（绝不删除）。meta 携带审计载荷（old/new hash、类型、前后字数）。"""
    new_text, types = pii.redact(row["content"])
    if not types:
        return ("keep",)
    from common import ids
    from common.textstats import text_stats

    new_row = dict(row)
    new_row["content"] = new_text
    new_row["content_hash"] = ids.content_hash(new_text)
    stats = text_stats(new_text)
    new_row["char_count"] = stats["char_count"]
    new_row["cjk_char_count"] = stats["cjk_char_count"]
    new_row["utf8_byte_count"] = stats["utf8_byte_count"]
    return ("transform", new_row, cfg["reason"], "redact", {
        "old_hash": row["content_hash"],
        "new_hash": new_row["content_hash"],
        "pii_types": types,
        "before_chars": row["char_count"],
        "after_chars": new_row["char_count"],
    })


def url_norm(row, cfg):
    """回填 canonical_url（去 fragment、host 小写、剥追踪参数、参数排序）。

    派生字段回填非内容变更——显式豁免于 changelog（M4-D3）。确定性：同 URL 同输出。
    """
    url = row.get("url")
    if not url:
        return ("keep",)
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    port = f":{parts.port}" if parts.port else ""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
              if not any(k.startswith(p) for p in cfg["strip_params_prefix"])]
    canonical = urlunsplit((parts.scheme.lower() or "https", f"{host}{port}",
                            parts.path or "/", urlencode(sorted(query)), ""))
    if canonical == row.get("canonical_url"):
        return ("keep",)
    new_row = dict(row)
    new_row["canonical_url"] = canonical
    return ("transform", new_row, cfg.get("reason", "url_normalized"), "modify", {})
