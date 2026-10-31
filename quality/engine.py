"""quality/engine.py — 五维打分 + 门禁（M6，规则版 v1；LLM 判官按砍单顺序后置）

分数语义统一：0~1，越高越好。每维一个纯函数；weighted total；门禁三分
（pass / hold(隔离待复核) / remove）。judge/蒸馏未实装——五维规则先扛（砍单序第 2 位）。
"""

import json
import math
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_PARA_SPLIT = re.compile(r"\n+")
_ENDING_PUNCT = tuple("。！？；.!?;")


@lru_cache(maxsize=1)
def policy() -> dict:
    return json.loads((ROOT / "configs" / "quality_policy.json").read_text(encoding="utf-8"))


def score_completeness(row: dict) -> float:
    """句式行的收尾完整度：以标点结尾的**句式行**（≥15 字符）占比。

    为什么只数句式行：真实语料混有节标题（「题解」「评价」）、列表项、引用行——
    它们本来就不以标点结尾，把它们算进分母会把结构当缺陷（M6 真实数据教训：
    27.4% 的维基文档被误杀，诊断见 DEVLOG）。截断文的特征是**长段无标点结尾**，
    本口径恰好只测这个。
    """
    paras = [p.strip() for p in _PARA_SPLIT.split(row["content"])
             if len(p.strip()) >= 15]
    if not paras:
        return 1.0   # 没有句式行（如纯链接页）→ 该维不作为证据，交给其他维度
    ok = sum(1 for p in paras if p.endswith(_ENDING_PUNCT))
    return ok / len(paras)


def score_uniqueness(row: dict, nearest_similarity: float | None = None) -> float:
    """唯一性：1 − 最近邻相似度。dedup 签名表的最近邻由调用方预算（未提供=1.0 顶格）。"""
    if nearest_similarity is None:
        return 1.0
    return max(0.0, 1.0 - nearest_similarity)


def score_consistency(row: dict) -> float:
    """一致性：残留乱码密度越低分越高（线性截断到 0）。"""
    cfg_max = policy()["dims"]["consistency"]["max_fffd_per_kchar"]
    per_k = (row["content"].count("\ufffd") * 1000 / row["char_count"]) if row["char_count"] else 0.0
    return max(0.0, 1.0 - per_k / cfg_max) if cfg_max else 1.0


def score_non_pollution(row: dict, index: set | None) -> float:
    """干净度：评测集 n-gram 命中密度映射（无索引=1.0 且调用方标 skipped）。"""
    if index is None:
        return 1.0
    n = policy()["dims"]["non_pollution"]["n"]
    text = row["content"]
    grams = [text[i:i + n] for i in range(max(len(text) - n + 1, 0))]
    if not grams:
        return 1.0
    hits = sum(1 for g in grams if g in index)
    density = hits / len(grams)
    return max(0.0, 1.0 - density * 100)  # 1% 命中密度即 0 分（污染是重罪）


def score_length(row: dict) -> float:
    """长度适宜度：体裁目标带内=1.0，带外对数距离衰减。"""
    bands = policy()["dims"]["length"]["genre_target_chars"]
    lo, hi = bands.get(row.get("genre"), bands["default"])
    c = row["char_count"]
    if lo <= c <= hi:
        return 1.0
    if c < lo:
        return max(0.0, math.log10(max(c, 1)) / math.log10(lo)) if lo > 1 else 0.0
    return max(0.0, 1.0 - math.log10(c / hi) / 2.0)  # 每超一个数量级扣一半


def score_row(row: dict, *, nearest_similarity: float | None = None,
              contamination_index: set | None = None) -> dict:
    """一行 → 五维分 + 加权总分。

    返回: {completeness, uniqueness, consistency, non_pollution, length, quality_score,
           pollution_skipped}——键名与 unified_schema stage_extensions.quality 对齐
    幂等: 纯函数
    """
    dims = {
        "quality_completeness": score_completeness(row),
        "quality_uniqueness": score_uniqueness(row, nearest_similarity),
        "quality_consistency": score_consistency(row),
        "quality_non_pollution": score_non_pollution(row, contamination_index),
        "quality_length": score_length(row),
    }
    weights = policy()["weights"]
    total = sum(dims[k] * weights[k.split("_", 1)[1]] for k in dims)
    return {**dims, "quality_score": round(total, 4),
            "pollution_skipped": contamination_index is None}


def gate(scores: dict) -> tuple:
    """门禁三分：(verdict, reason)。verdict ∈ pass/hold/remove。

    remove: 总分 < quarantine_band[0] 或任一维 < min_dim_score（reason=low_quality）
    hold:    总分在隔离带内（reason=quarantine_borderline，changelog action=quarantine）
    """
    g = policy()["gate"]
    band_lo, band_hi = g["quarantine_band"]
    if scores["quality_score"] < band_lo or any(
            scores[k] < g["min_dim_score"] for k in scores if k.startswith("quality_") and k != "quality_score"):
        return "remove", "low_quality"
    if scores["quality_score"] < g["min_total_score"] or scores["quality_score"] < band_hi:
        return "hold", "quarantine_borderline"
    return "pass", None


def build_ngram_index(benchmark_paths: list) -> set:
    """评测集 → n-gram 哈希集合（污染检测的子弹库）。

    参数: benchmark_paths — jsonl 文件（每行 {"text": ...} 或纯文本行）
    返回: n-gram 字符串集合；文件全缺 → 空集合（调用方按 skipped 处理——不静默假装检测过）
    幂等: 纯读
    """
    n = policy()["dims"]["non_pollution"]["n"]
    index = set()
    for path in benchmark_paths:
        p = Path(path)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
                text = obj.get("text", "") if isinstance(obj, dict) else text
            except json.JSONDecodeError:
                pass
            for i in range(max(len(text) - n + 1, 0)):
                index.add(text[i:i + n])
    return index
