"""dedup/engine.py — 精确 + 近重复去重引擎（M5）

两级：
1. exact：normalized_content_hash 分组（跨格式/全半角/简繁折叠后的内容指纹）；
2. near：MinHash LSH 候选 → 真实 Jaccard 复核（LSH 是召回手段不是判决）→ 并查集成簇。

簇代表（canonical）选择：源可信度 → 内容长 → doc_id 字典序（确定性铁律）。
全部纯函数；IO 与阶段编排见 dedup/run.py。
"""

import base64
import json
from functools import lru_cache
from pathlib import Path

from datasketch import MinHash, MinHashLSH

ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def policy() -> dict:
    return json.loads((ROOT / "configs" / "dedup_policy.json").read_text(encoding="utf-8"))


def shingles(text: str, unit: str = "char", n: int = 3) -> set:
    """中文按字符 n-gram 切分。

    参数: text — 已规范化前的原文（MinHash 用规范化后文本，见 run.py）；unit/n — 来自 policy
    返回: shingle 字符串集合
    异常: ValueError — unit 不支持
    幂等: 纯函数
    """
    if unit == "char":
        return {text[i:i + n] for i in range(max(len(text) - n + 1, 0))} or ({text} if text else set())
    if unit == "word":
        words = text.split()
        return {" ".join(words[i:i + n]) for i in range(max(len(words) - n + 1, 0))}
    raise ValueError(f"不支持的 shingle unit: {unit}")


def minhash_signature(text: str, num_perm: int) -> MinHash:
    """规范化文本 → MinHash 签名。"""
    m = MinHash(num_perm=num_perm)
    for s in shingles(text, policy()["shingle"]["unit"], policy()["shingle"]["n"]):
        m.update(s.encode("utf-8"))
    return m


def signature_to_b64(m: MinHash) -> str:
    """签名序列化为 base64（dedup_signature_schema 的存储口径；Parquet 落 binary 前的交换格式）。"""
    return base64.b64encode(m.hashvalues.tobytes()).decode("ascii")


class UnionFind:
    """并查集（簇合并）。路径压缩 + 按秩合并。确定性：合并方向按根的字节序，与插入顺序无关。"""

    def __init__(self):
        self.parent, self.rank = {}, {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if (self.rank.get(ra, 0), ra) < (self.rank.get(rb, 0), rb):  # 秩平则根字典序小者为父
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank.get(ra, 0) == self.rank.get(rb, 0):
            self.rank[ra] = self.rank.get(ra, 0) + 1

    def clusters(self) -> dict:
        out = {}
        for node in self.parent:
            out.setdefault(self.find(node), []).append(node)
        return out


def dedup_pass(rows: list) -> dict:
    """主算法：行列表（dict，含 doc_id/source/content/normalized hash 由调用方预算）→ 报告。

    参数: rows — [{doc_id, source, content, normalized_hash, char_count}, ...]
    返回: {"keep": [row...], "removed": [{row, reason, ref_doc_id, similarity, cluster_id}...],
           "clusters": {cluster_id: [doc_id...]}, "cross_source": {(a,b): n}}
    异常: 行缺 doc_id/content → KeyError（契约破坏 fail-fast）
    幂等: 纯算法 + 全字典序 tiebreak；同输入同输出
    """
    pol = policy()

    # ---- 1) exact：规范化哈希分组
    by_hash = {}
    for row in rows:
        by_hash.setdefault(row["normalized_hash"], []).append(row)
    removed, keep = [], []
    uf = UnionFind()
    exact_groups = 0
    for h, group in by_hash.items():
        if len(group) == 1:
            continue
        exact_groups += 1
        canon = _pick_canonical(group, pol["canonical_policy"])
        for row in group:
            if row["doc_id"] == canon["doc_id"]:
                continue
            removed.append({"row": row, "reason": "exact_dup", "ref_doc_id": canon["doc_id"],
                            "similarity": 1.0, "cluster_id": f"exact:{h[:12]}"})
        for row in group[1:]:
            uf.union(group[0]["doc_id"], row["doc_id"])

    # ---- 2) near：在 exact 幸存者上做 MinHash LSH（exact 已删的行不再参与，省算力）
    survivors = [r for r in rows if r["doc_id"] not in {x["row"]["doc_id"] for x in removed}]
    lsh = MinHashLSH(num_perm=pol["minhash"]["num_perm"], params=(pol["lsh"]["bands"],
                          pol["minhash"]["num_perm"] // pol["lsh"]["bands"]))
    sigs = {}
    for row in survivors:
        m = minhash_signature(row["content"], pol["minhash"]["num_perm"])
        sigs[row["doc_id"]] = m
        lsh.insert(row["doc_id"], m)

    threshold = pol["verify_threshold"]
    near_pair_seen = set()
    for row in survivors:
        candidates = lsh.query(sigs[row["doc_id"]])
        for other in sorted(candidates):  # 排序保证处理顺序确定
            if other <= row["doc_id"]:
                continue
            pair = (row["doc_id"], other)
            if pair in near_pair_seen:
                continue
            near_pair_seen.add(pair)
            sim = sigs[row["doc_id"]].jaccard(sigs[other])
            if sim >= threshold:
                uf.union(pair[0], pair[1])

    # ---- 3) 簇代表 + 近重复删除记录
    doc_index = {r["doc_id"]: r for r in rows}
    removed_ids = {x["row"]["doc_id"] for x in removed}
    near_removed_map = {}
    for root, members in uf.clusters().items():
        if len(members) < 2:
            continue
        alive = [doc_index[m] for m in members if m not in removed_ids]
        if len(alive) < 2:
            continue
        canon = _pick_canonical(alive, pol["canonical_policy"])
        cluster_id = f"near:{min(members)[:12]}"
        for row in alive:
            if row["doc_id"] == canon["doc_id"]:
                continue
            near_removed_map[row["doc_id"]] = {
                "row": row, "reason": "near_dup", "ref_doc_id": canon["doc_id"],
                "similarity": round(sigs[row["doc_id"]].jaccard(sigs[canon["doc_id"]]), 4),
                "cluster_id": cluster_id,
            }

    removed.extend(near_removed_map.values())

    # ---- 4) 汇总
    kept = [r for r in rows if r["doc_id"] not in {x["row"]["doc_id"] for x in removed}]
    clusters = {cid: sorted(members) for cid, members in uf.clusters().items() if len(members) >= 2}
    cross = {}
    for members in clusters.values():
        srcs = sorted({doc_index[m]["source"] for m in members if m in doc_index})
        for i in range(len(srcs)):
            for j in range(i + 1, len(srcs)):
                cross[(srcs[i], srcs[j])] = cross.get((srcs[i], srcs[j]), 0) + 1
    return {"keep": kept, "removed": removed, "clusters": clusters,
            "cross_source": cross, "exact_groups": exact_groups,
            "signatures": {doc_id: signature_to_b64(m) for doc_id, m in sigs.items()}}


def _pick_canonical(group: list, canonical_policy: dict):
    """簇代表：源信任序 → 内容长降序 → doc_id 升序。纯确定性。"""
    trust = {s: i for i, s in enumerate(canonical_policy["source_trust"])}

    def key(row):
        return (trust.get(row["source"], len(trust)), -row.get("char_count", 0), row["doc_id"])

    return sorted(group, key=key)[0]
