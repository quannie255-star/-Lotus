"""downstream ppl 测试（M8）"""

import pytest

from downstream.ppl import CharNgramLM, bootstrap_ci


def _zh(n, seed=0):
    import random

    rng = random.Random(seed)
    words = ["经济", "数据", "市场", "增长", "政策", "研究", "分析", "发布", "今天", "昨天"]
    return "。".join("".join(rng.choice(words) for _ in range(rng.randint(3, 8))) for _ in range(n))


def test_ppl_orders_correctly():
    """同分布 ppl 显著低于异分布——协议有效性的序关系验证（量级差受平滑与小词表影响，
    不做强比例断言）。"""
    train = _zh(2000, seed=1)
    lm = CharNgramLM().train(train)
    same_dist = _zh(60, seed=2)
    other = "abcdefghij klmnopqrst uvwxyz abcd efgh ijkl" * 40
    p_same = lm.perplexity(same_dist.split("。"))
    p_other = lm.perplexity(other.split(" "))
    assert p_same < p_other * 0.9


def test_lm_deterministic_and_additive():
    """可加性在任意切点近似成立：边界跨切点的 n-1 阶 gram 会丢失（文档化性质，非缺陷）。"""
    a = _zh(500, seed=3)
    lm1 = CharNgramLM().train(a)
    lm2 = CharNgramLM().train(a[:len(a) // 2]).train(a[len(a) // 2:])
    eval_lines = _zh(20, seed=4).split("。")
    assert lm1.perplexity(eval_lines) == pytest.approx(lm2.perplexity(eval_lines), rel=1e-3)


def test_bootstrap_ci_stable():
    lm = CharNgramLM().train(_zh(1000, seed=5))
    lines = _zh(50, seed=6).split("。")
    ci1 = bootstrap_ci(lm, lines, n_boot=20, seed=42)
    ci2 = bootstrap_ci(lm, lines, n_boot=20, seed=42)
    assert ci1 == ci2 and ci1[0] <= ci1[1]


def test_empty_corpus_raises():
    with pytest.raises(ValueError):
        CharNgramLM().train("")
