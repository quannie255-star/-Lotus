"""metrics/card.py — 数据质量指标卡（Tier 0：「看得见」的仪表盘本体）

对一个数据版本（一组 parquet part）计算质量指标卡。定位是**控制器读数**：
治理前后各拍一张卡，diff 就是这台控制台的显示面板。

口径纪律：
- 全量指标（n_docs / total_chars）精确计算；
- 内容指标（重复率/完整性/一致性/冗余）基于**确定性抽样**（seed 固定，默认 20k 行）
  ——23 万行逐条规范化是 75 分钟级操作，读数不需要这个代价；抽样使指标卡分钟级可刷。
  卡片里显式记录 sampled_rows，不冒充全量。
- 语义多样性 v0 为 null（embedding 未接入，roadmap 项）——空读数不假装有数。
"""

import json
import random
import re
from pathlib import Path

import pyarrow.parquet as pq

from common import ids
from quality.engine import score_completeness

SAMPLE_ROWS = 20_000
SAMPLE_SEED = 2026
_PARA_SPLIT = re.compile(r"\n+")
_URL_RE = re.compile(r"https?://\S+")


def _iter_rows(parts: list):
    for path in parts:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=2000):
            yield from batch.to_pylist()


def compute_card(parts: list, label: str, changelog_path: Path | None = None) -> dict:
    """对一个数据版本计算质量指标卡。

    参数: parts — parquet 文件列表；label — 版本标签（如 v0-raw / v1-release）；
          changelog_path — 提供则计算治理口径的脏样本拒收率
    返回: 卡片 dict（见 KEYS；语义多样性 v0=None 待接入）
    异常: parts 为空 → ValueError
    幂等: 纯读 + 确定性抽样；同输入同卡
    """
    if not parts:
        raise ValueError(f"[{label}] 没有可计算的数据文件")

    n_docs, total_chars = 0, 0
    sample = []
    rng = random.Random(SAMPLE_SEED)
    reservoir_limit = SAMPLE_ROWS
    for row in _iter_rows(parts):
        n_docs += 1
        total_chars += row.get("char_count", len(row.get("content", "")))
        if len(sample) < reservoir_limit:
            sample.append(row)
        else:  # 蓄水池抽样（确定性：rng 状态由行序驱动，行序由文件序+批序确定）
            j = rng.randrange(n_docs)
            if j < reservoir_limit:
                sample[j] = row

    # 精确重复率：规范化哈希一次预计算 + seen 集合（O(n) 哈希，不是 O(n²)——
    # 定义：与在它之前出现的行内容规范化后相同的行占比）
    seen_hashes = set()
    exact_dup = 0
    for r in sample:
        h = ids.normalized_content_hash(r["content"])
        if h in seen_hashes:
            exact_dup += 1
        else:
            seen_hashes.add(h)
    completeness = sum(score_completeness(r) for r in sample) / len(sample)
    han_ratio = sum(
        (r["cjk_char_count"] / r["char_count"]) if r.get("char_count") else 0.0
        for r in sample) / len(sample)
    redundancy = sum(_internal_redundancy(r["content"]) for r in sample) / len(sample)
    quality_scores = [r["quality_score"] for r in sample if r.get("quality_score") is not None]

    card = {
        "label": label,
        "n_docs": n_docs,
        "total_chars": total_chars,
        "sampled_rows": len(sample),
        "exact_dup_rate": round(exact_dup / len(sample), 5),
        "format_completeness": round(completeness, 4),
        "language_consistency": round(han_ratio, 4),
        "internal_redundancy": round(redundancy, 4),
        "info_density_proxy": round(1.0 - redundancy, 4),
        "mean_quality_score": round(sum(quality_scores) / len(quality_scores), 4) if quality_scores else None,
        "semantic_diversity": None,  # v0 未接入 embedding，空读数不假装有数
        "dirty_reject_rate": _dirty_reject_rate(changelog_path) if changelog_path else None,
        "notes": [
            "内容指标基于确定性蓄水池抽样(≤20k, seed=2026)；n_docs/total_chars 为全量",
            "exact_dup_rate = 抽样内规范化哈希重复的行占比（精确口径；近似/语义重复见 dedup 报告）",
            "info_density_proxy v0 = 1−文档内重复行字符占比（正式信息密度定义待 Tier 1）",
        ],
    }
    return card


def _internal_redundancy(content: str) -> float:
    """文档内冗余 v0：重复行（去首尾空白后相同）字符占比。"""
    lines = [l.strip() for l in _PARA_SPLIT.split(content) if l.strip()]
    if not lines:
        return 0.0
    seen, dup_chars, total = set(), 0, sum(len(l) for l in lines)
    for line in lines:
        if line in seen:
            dup_chars += len(line)
        else:
            seen.add(line)
    return dup_chars / total if total else 0.0


def _dirty_reject_rate(changelog_path: Path | None) -> float | None:
    """治理口径脏样本拒收率：clean 阶段 remove / (clean 放行 + remove)。"""
    if not changelog_path or not Path(changelog_path).exists():
        return None
    removed = kept = 0
    for line in Path(changelog_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("stage") == "clean" and rec.get("action") == "remove":
            removed += 1
        elif rec.get("stage") == "clean" and rec.get("action") == "redact":
            kept += 1
    return round(removed / (removed + kept), 4) if (removed + kept) else None


def compare_cards(before: dict, after: dict) -> dict:
    """治理前后对比卡：数值指标算 Δ 与方向（↑好/↓好按指标语义标注）。"""
    direction = {  # True=越高越好
        "format_completeness": True, "language_consistency": True,
        "info_density_proxy": True, "mean_quality_score": True,
        "exact_dup_rate": False, "internal_redundancy": False, "dirty_reject_rate": False,
    }
    rows = {}
    for key, higher_is_better in direction.items():
        a, b = before.get(key), after.get(key)
        if a is None or b is None:
            continue
        delta = round(b - a, 5)
        rows[key] = {"before": a, "after": b, "delta": delta,
                     "verdict": ("改善" if (delta > 0) == higher_is_better and delta != 0
                                 else "恶化" if delta != 0 else "持平")}
    rows["n_docs"] = {"before": before["n_docs"], "after": after["n_docs"],
                      "delta": after["n_docs"] - before["n_docs"], "verdict": "规模"}
    return {"before": before["label"], "after": after["label"], "metrics": rows}
