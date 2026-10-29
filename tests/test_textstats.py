"""common/textstats.py 的测试：三个计数是纯函数，口径必须与 schema 字段描述一致。"""

import pytest

from common.textstats import cjk_char_count, text_stats


def test_counts_are_code_points_not_bytes():
    """char_count 数 Unicode 码点：'数学A' 是 3，不是 3 字节、也不是 6。"""
    stats = text_stats("数学A")
    assert stats["char_count"] == 3
    assert stats["cjk_char_count"] == 2
    assert stats["utf8_byte_count"] == len("数学A".encode("utf-8")) == 7


def test_cjk_punctuation_is_not_counted_as_han():
    """。、「」是 CJK 标点，不是汉字——口径写清楚，下游长度过滤才不会跑偏。"""
    assert cjk_char_count("。、「」《》") == 0
    assert cjk_char_count("你好，世界！") == 4


def test_traditional_and_extension_blocks_are_han():
    """繁体（學）和扩展区（㐀 = U+3400）都算汉字——中文语料混排繁简是常态。"""
    assert cjk_char_count("數學") == 2
    assert cjk_char_count("\u3400\U00020000") == 2


def test_zero_length_text():
    assert text_stats("") == {"char_count": 0, "cjk_char_count": 0, "utf8_byte_count": 0}


def test_rejects_non_str():
    with pytest.raises(TypeError):
        text_stats(None)
    with pytest.raises(TypeError):
        cjk_char_count(b"bytes")
