"""clean/pii.py — PII 识别与脱敏（T3）

顺序永远是：先识别（detect），再替换（redact）。识别结果要能落进 changelog，
替换只是副作用；一上来就 re.sub 会丢掉"改了什么"的审计能力。

四类目标：
- id_card    18 位，前 17 位加权求和 % 11 校验（GB 11643 / ISO 7064 MOD 11-2）+ 出生日期合法性
- bank_card  16–19 位 + Luhn 校验（不校验就会把订单号当卡号）
- phone      中国大陆手机号 1[3-9] 开头 11 位，前后不能是数字
- email      常规邮箱形态

长串数字容易被多个正则同时命中：按 id_card → bank_card → phone → email 的顺序吃，
已被覆盖的区间不再重复命中。
"""

import re
from datetime import date

# 先用宽松形态找候选，再用校验函数过滤——只用一个"完美正则"是做不到的
ID_CARD_RE = re.compile(r"(?<![0-9A-Za-z])\d{17}[0-9Xx](?![0-9A-Za-z])")
BANK_CARD_RE = re.compile(r"(?<![0-9])\d{16,19}(?![0-9])")
PHONE_RE = re.compile(r"(?<![0-9])1[3-9]\d{9}(?![0-9])")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
ID_CHECK_CODES = "10X98765432"

# 脱敏保留位：(前, 后)；email 单独处理
MASK_SPANS = {"id_card": (3, 4), "bank_card": (4, 4), "phone": (3, 4)}


def validate_id_card(text):
    """第 18 位 = 前 17 位按位权加权和 % 11 查表；同时校验出生日期真实存在。"""
    if len(text) != 18 or not text[:17].isdigit():
        return False

    try:
        date(int(text[6:10]), int(text[10:12]), int(text[12:14]))
    except ValueError:
        return False

    total = sum(int(ch) * w for ch, w in zip(text[:17], ID_WEIGHTS))
    return ID_CHECK_CODES[total % 11] == text[17].upper()


def luhn_ok(text):
    """Luhn 校验：从右往左，偶数位（0 起）乘 2，大于 9 则减 9，总和 % 10 == 0。"""
    digits = [int(ch) for ch in text if ch.isdigit()]
    if not 12 <= len(digits) <= 19:
        return False

    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _overlaps(start, end, taken):
    return any(start < t_end and end > t_start for t_start, t_end in taken)


def detect(text):
    """返回命中列表：[(type, start, end, matched_text), ...]，按出现位置排序。"""
    checks = (
        ("id_card", ID_CARD_RE, validate_id_card),
        ("bank_card", BANK_CARD_RE, luhn_ok),
        ("phone", PHONE_RE, lambda s: True),
        ("email", EMAIL_RE, lambda s: True),
    )

    found = []
    taken = []
    for kind, pattern, validator in checks:
        for match in pattern.finditer(text):
            if not validator(match.group(0)):
                continue
            start, end = match.span()
            if _overlaps(start, end, taken):
                continue
            taken.append((start, end))
            found.append((kind, start, end, match.group(0)))

    return sorted(found, key=lambda item: item[1])


def _mask(value, head, tail):
    if len(value) <= head + tail:
        return "*" * len(value)
    return value[:head] + "*" * (len(value) - head - tail) + value[-tail:]


def redact(text):
    """脱敏，返回 (新文本, 命中类型列表)。类型列表用于写 changelog 的 pii_types。"""
    hits = detect(text)
    if not hits:
        return text, []

    pieces = []
    cursor = 0
    types = []
    for kind, start, end, value in hits:
        pieces.append(text[cursor:start])
        if kind == "email":
            local, _, domain = value.partition("@")
            pieces.append((local[:1] or "*") + "***@" + domain)
        else:
            head, tail = MASK_SPANS[kind]
            pieces.append(_mask(value, head, tail))
        cursor = end
        if kind not in types:
            types.append(kind)

    pieces.append(text[cursor:])
    return "".join(pieces), types
