"""common/ids.py — 文档 ID 与内容哈希（T1）

三个函数职责不同，别混：
- spine_doc_id        身份：sha256(source + ":" + source_id)，64 位 hex，一经发布永不复算
- content_hash        内容指纹：sha256(utf8(content))，不做任何规范化
- normalized_content_hash  去重指纹：按 configs/text_normalization.json 规范化后再哈希

唯一规则来源是 config 文件；本文件里没有硬编码的"规则值"，
只硬编码"规则怎么执行"（translate / normalize / 正则）。
"""

import hashlib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "text_normalization.json"

# NFKC 不负责的空白形态（NBSP、en/em space、表意空格等），由 whitespace 规则统一成半角空格
UNICODE_SPACES = "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000"


@lru_cache(maxsize=1)
def load_norm_config():
    """读取规范化配置（只读一次，之后走缓存）。"""
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _sha256_hex(text):
    """字符串 → UTF-8 字节 → sha256 → 64 位小写 hex。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(content):
    """内容指纹：不做任何规范化。'ABC' 与 'abc' 在这里是两个不同的值。"""
    if not isinstance(content, str):
        raise TypeError("content 必须是 str")
    return _sha256_hex(content)


# --------------------------------------------------------------------------
# 单条规范化步骤：签名统一为 (text, cfg) -> text
# 顺序不在这里写死，由 config 的 pipeline_order 决定
# --------------------------------------------------------------------------

def _remove_zero_width(text, cfg):
    """删除零宽字符（U+200B/200C/200D/FEFF 等）。"""
    if not cfg.get("remove_zero_width"):
        return text
    return text.translate({ord(ch): None for ch in cfg.get("zero_width_chars", [])})


def _remove_control_chars(text, cfg):
    """删除 C0/C1 控制符，但放行 control_chars_keep（换行、制表、回车）。"""
    if not cfg.get("remove_control_chars"):
        return text
    keep = set(cfg.get("control_chars_keep", []))

    def is_drop(ch):
        if ch in keep:
            return False
        code = ord(ch)
        return code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F

    return "".join(ch for ch in text if not is_drop(ch))


def _nfkc(text, cfg):
    """NFKC 归一：全角字母数字与兼容字符折叠成半角。"""
    form = cfg.get("unicode_form", "none")
    return text if form == "none" else unicodedata.normalize(form, text)


def _case_folding(text, cfg):
    """大小写折叠（中文不受影响）。"""
    mode = cfg.get("case_folding", "none")
    if mode == "lower":
        return text.lower()
    if mode == "casefold":
        return text.casefold()
    return text


def _trad_to_simp(text, cfg):
    """繁简折叠；只用于哈希，绝不回写 content。"""
    if not cfg.get("fold_traditional_to_simplified"):
        return text
    from zhconv import convert  # 延迟导入：只有真要用时才加载词典

    locale = {"t2s": "zh-cn", "s2t": "zh-tw"}[cfg.get("traditional_to_simplified_mode", "t2s")]
    return convert(text, locale)


def _punctuation(text, cfg):
    """CJK/通用标点 → ASCII（只映射 NFKC 不处理的那些）。"""
    punct = cfg.get("punctuation") or {}
    if not punct.get("enabled"):
        return text
    return text.translate({ord(k): v for k, v in (punct.get("map") or {}).items()})


def _whitespace(text, cfg):
    """空白收尾：Unicode 空格归一 → 行尾归一 → 折叠 → trim。必须最后执行。"""
    ws = cfg["whitespace"]

    if ws.get("normalize_unicode_spaces"):
        text = text.translate({ord(ch): " " for ch in UNICODE_SPACES})

    line_ending = ws.get("line_ending", "LF")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    if ws.get("collapse_runs"):
        if ws.get("collapse_scope", "horizontal") == "all":
            text = re.sub(r"\s+", " ", text)
        else:
            text = re.sub(r"[ \t]+", " ", text)

    if ws.get("trim"):
        text = text.strip()

    return text.replace("\n", "\r\n") if line_ending == "CRLF" else text


_STEP_FUNCS = {
    "remove_zero_width": _remove_zero_width,
    "remove_control_chars": _remove_control_chars,
    "unicode_nfkc": _nfkc,
    "case_folding": _case_folding,
    "trad_to_simp": _trad_to_simp,
    "punctuation": _punctuation,
    "whitespace": _whitespace,
}


def normalize_text(text):
    """按 pipeline_order 依次执行，返回规范化后的纯文本。"""
    if not isinstance(text, str):
        raise TypeError("text 必须是 str")

    cfg = load_norm_config()
    for step in cfg["pipeline_order"]:
        func = _STEP_FUNCS.get(step)
        if func is None:
            raise ValueError(f"config 里的未知规范化步骤: {step}")
        text = func(text, cfg)
    return text


def normalized_content_hash(content):
    """去重指纹：规范化之后再做 sha256，按 config 的 truncate 决定是否截断。"""
    cfg = load_norm_config()
    digest = _sha256_hex(normalize_text(content))
    truncate = cfg["hash"].get("truncate")
    return digest[:truncate] if truncate else digest


def spine_doc_id(source, source_id):
    """身份 ID：只由 (source, source_id) 决定，与 URL、内容、时间无关。"""
    if not source or not source_id:
        raise ValueError("source 与 source_id 都必填")
    return _sha256_hex(f"{source}:{source_id}")
