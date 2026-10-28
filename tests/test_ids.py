"""T1 验收测试：五个场景 + 三个哈希函数的行为边界。"""

from common.ids import (
    content_hash,
    load_norm_config,
    normalize_text,
    normalized_content_hash,
    spine_doc_id,
)


def test_config_is_readable():
    cfg = load_norm_config()
    assert cfg["version"] == "1.0.0"
    assert cfg["pipeline_order"][-1] == "whitespace"


def test_idempotent():
    """同输入两次，哈希必须完全一样。"""
    assert normalized_content_hash("一段中文文本") == normalized_content_hash("一段中文文本")


def test_fullwidth_halfwidth():
    """全角 ＡＢＣ１２３ 与半角 ABC123 视为同一内容。"""
    assert normalized_content_hash("ＡＢＣ１２３") == normalized_content_hash("ABC123")


def test_case_and_whitespace():
    """大小写、多空格、首尾空格都不影响去重哈希。"""
    assert normalized_content_hash("  Hello   World  ") == normalized_content_hash("hello world")


def test_traditional_simplified():
    """繁体与简体视为同一内容（t2s）。"""
    assert normalized_content_hash("漢字") == normalized_content_hash("汉字")


def test_different_content_differs():
    assert normalized_content_hash("abc") != normalized_content_hash("abd")


def test_punctuation_folding():
    """CJK 标点折叠成 ASCII 后，两种写法同哈希。"""
    assert normalized_content_hash("他说：“好”。") == normalized_content_hash('他说:"好".')


def test_content_hash_does_not_normalize():
    """content_hash 是"原始内容指纹"，大小写不同必须不同。"""
    assert content_hash("ABC") != content_hash("abc")
    assert content_hash("ABC") == content_hash("ABC")


def test_hash_shape():
    """两个哈希都是完整 64 位 hex（截断决策为 null）。"""
    for digest in (content_hash("x"), normalized_content_hash("x"), spine_doc_id("wiki", "1")):
        assert len(digest) == 64
        int(digest, 16)


def test_spine_doc_id_is_deterministic():
    assert spine_doc_id("wiki", "rev-1") == spine_doc_id("wiki", "rev-1")
    assert spine_doc_id("wiki", "rev-1") != spine_doc_id("news", "rev-1")


def test_newlines_are_preserved():
    """collapse_scope=horizontal：段落边界不能被压成空格。"""
    assert normalize_text("第一段\n\n第二段") == "第一段\n\n第二段"
