"""ingest/sampling.py — 确定性分片抽样（DEVLOG M2-D2，契约源：WP-01 §2.2 前身）

大源（OSCAR zh 全量 ~x GB）首跑抽样：下载层按分片清单做确定性抽样（种子+分片哈希排序），
选中清单固化为不可变 manifest——manifest 即 run_stage 的输入指纹，处理级幂等白拿。

为什么是整群抽样而不是行级随机：行级抽样要求先下载全量（成本已付），且样本依赖读取
顺序，可复现性弱。代价是分片间可能有系统性偏差（如按爬取时间排序）——机制验证够用，
W4 参数实验要记进局限（blueprint §3 E1 注）。
"""

import hashlib
import json
from pathlib import Path


def sample_shards(all_shards: list, k: int, seed: int) -> list:
    """确定性抽样：sha256(f"{seed}:{shard}") 升序取前 k。

    参数: all_shards — 分片名/路径全量清单；k — 抽取数；seed — 随机种子（进 git）
    返回: 选中的 k 个分片（按哈希序，与输入顺序无关）
    异常: ValueError — k 不在 1..len(all_shards)
    幂等: 纯函数；同 (all_shards, k, seed) 永远同结果
    """
    if not 1 <= k <= len(all_shards):
        raise ValueError(f"k 必须在 1..{len(all_shards)}，收到 {k}")
    keyed = sorted(all_shards, key=lambda s: hashlib.sha256(f"{seed}:{s}".encode("utf-8")).hexdigest())
    return keyed[:k]


def write_sample_manifest(selected: list, formula: str, path: Path) -> None:
    """抽样决策落盘为不可变快照（进 git = 可复现）。

    参数: selected — 选中清单；formula — 复原公式描述（如 "sample_shards(seed=42, k=2)"）；path — 输出
    返回: 无
    异常: OSError — 写盘失败（.tmp 写入失败时目标文件保持旧版）
    幂等: 内容确定 → 同输入产出字节一致（先 .tmp 后 replace）
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"formula": formula, "count": len(selected), "selected": list(selected)},
        ensure_ascii=False, indent=2, sort_keys=True,
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
