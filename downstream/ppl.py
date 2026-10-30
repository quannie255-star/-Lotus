"""downstream/ppl.py — 下游困惑度验证（M8，纯 Python 字符 n-gram LM）

协议（blueprint §2.9/E5，防两个经典混淆因子）：
- 不是「在 A 上算 ppl vs 在 B 上算 ppl」（文本分布不同不可比）；
- 而是「用 A 训 LM₁、用 B 训 LM₂，两个模型在同一**冻结评测集**上比 ppl」；
- 报 bootstrap 置信区间，不报裸点估。

N7 决策：kenlm 在 Windows 需要 MSVC 构建链 → v1 用纯 Python 5-gram add-k 平滑
（确定性、零依赖、慢但可跑）；口径记录在 report（engine 标识）。
"""

import math
import random
from collections import defaultdict
from pathlib import Path

ORDER = 5
ALPHA = 0.01          # add-k 平滑常数
EVAL_LINE_MAX = 400   # 评测行截断（超长行截断，口径记录）


class CharNgramLM:
    """字符 n-gram 语言模型（add-k 平滑）。训练语料的规模上限见 train() 的 max_chars。"""

    def __init__(self, order: int = ORDER, alpha: float = ALPHA):
        self.order, self.alpha = order, alpha
        self.counts = defaultdict(int)
        self.contexts = defaultdict(int)
        self.vocab = set()
        self.trained_chars = 0

    def train(self, text: str, max_chars: int | None = None) -> "CharNgramLM":
        """在语料上计数训练。

        参数: text — 训练语料；max_chars — 截断上限（控制训练时长，口径进报告）
        返回: self
        异常: 空语料 → ValueError
        幂等: 追加式训练；同语料同模型（计数顺序无关）
        """
        if max_chars:
            text = text[:max_chars]
        if not text:
            raise ValueError("训练语料为空")
        self.trained_chars += len(text)
        n = self.order
        for i in range(len(text) - n + 1):
            ctx, ch = text[i:i + n - 1], text[i + n - 1]
            self.counts[(ctx, ch)] += 1
            self.contexts[ctx] += 1
            self.vocab.add(ch)
        return self

    def log2_prob(self, ctx: str, ch: str) -> float:
        v = max(len(self.vocab), 1)
        num = self.counts[(ctx, ch)] + self.alpha
        den = self.contexts.get(ctx, 0) + self.alpha * v
        return math.log2(num / den)

    def perplexity(self, lines: list) -> float:
        """评测集（行列表）上的困惑度。

        参数: lines — 文本行；每行截断到 EVAL_LINE_MAX
        返回: 2^(−mean log2 p)；无有效字符 → ValueError
        幂等: 纯读
        """
        n = self.order
        total_ll, total_ch = 0.0, 0
        for line in lines:
            line = line[:EVAL_LINE_MAX]
            padded = "\u0002" * (n - 1) + line + "\u0003"
            for i in range(len(padded) - n + 1):
                ctx, ch = padded[i:i + n - 1], padded[i + n - 1]
                total_ll += self.log2_prob(ctx, ch)
                total_ch += 1
        if total_ch == 0:
            raise ValueError("评测集无有效字符")
        return 2 ** (-total_ll / total_ch)


def bootstrap_ci(model: CharNgramLM, lines: list, n_boot: int = 50,
                 sample_frac: float = 0.8, seed: int = 42) -> tuple:
    """评测行重采样给 ppl 的 95% 置信区间（percentile 法）。

    幂等: 随机全由 seed 驱动。
    """
    rng = random.Random(seed)
    k = max(int(len(lines) * sample_frac), 1)
    scores = []
    for _ in range(n_boot):
        sample = [lines[rng.randrange(len(lines))] for _ in range(k)]
        scores.append(model.perplexity(sample))
    scores.sort()
    lo = scores[int(0.025 * len(scores))]
    hi = scores[min(int(0.975 * len(scores)), len(scores) - 1)]
    return round(lo, 2), round(hi, 2)


def corpus_text(parquet_paths: list, max_chars: int, seed: int = 42) -> str:
    """从阶段产物抽取确定性训练语料（行级洗牌后截断——避免只吃到字典序头部）。

    参数: parquet_paths — 阶段 part 文件；max_chars — 语料上限
    返回: 拼接语料文本
    """
    import pyarrow.parquet as pq

    rng = random.Random(seed)
    lines = []
    for path in parquet_paths:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=500, columns=["content"]):
            lines.extend(v for v in batch.column("content").to_pylist() if v)
    rng.shuffle(lines)
    out = []
    total = 0
    for text in lines:
        out.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return "\n".join(out)[:max_chars]
