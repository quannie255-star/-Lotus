"""common/textstats.py — 内容长度的确定性计数

三个数字（char / cjk / utf8 字节）是**纯函数**，只由 content 字符串决定，
不含任何判断：不做语言识别、不做长度过滤、不进 changelog。
所以它们可以放在 common 里被多个适配器共用——T6 的 oscar/news 需要同一套口径，
各写一份迟早会有一个源把「码点」当成「字节」。

口径（与 schema/unified_schema.json 的字段描述逐条对齐）：
- char_count      Unicode code points，含标点与拉丁字符 → len(str)
- cjk_char_count  汉字数（Han 书写系统），非中文源可为 0
- utf8_byte_count UTF-8 字节数，存储成本估算用

汉字判定：Python 的 re 不支持 \\p{Script=Han}，这里用 Han 在 Unicode 里的码位区间近似。
故意**不**收 U+3000–U+303F 的 CJK 标点（。、「」等属于标点不是汉字），
也不收 U+2E80 附近的部首补充——它们不是正文用字。
"""

import re

# 汉字主要区间：扩展 A、基本区、兼容汉字、扩展 B–F、扩展 G
_HAN = re.compile(
    "["
    "\u3400-\u4DBF"          # CJK Extension A
    "\u4E00-\u9FFF"          # CJK Unified Ideographs
    "\uF900-\uFAFF"          # CJK Compatibility Ideographs
    "\U00020000-\U0002FA1F"  # Extension B–F
    "\U00030000-\U0003134F"  # Extension G
    "]"
)


def cjk_char_count(text):
    """汉字个数；只数汉字，不数中文标点。"""
    if not isinstance(text, str):
        raise TypeError("text 必须是 str")
    return len(_HAN.findall(text))


def text_stats(text):
    """返回 {"char_count", "cjk_char_count", "utf8_byte_count"}，键名与 spine 字段同名。"""
    if not isinstance(text, str):
        raise TypeError("text 必须是 str")
    return {
        "char_count": len(text),
        "cjk_char_count": cjk_char_count(text),
        "utf8_byte_count": len(text.encode("utf-8")),
    }
