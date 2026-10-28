"""T3 测试：校验算法先于正则、长数字串不误判、脱敏形态。"""

from clean.pii import detect, luhn_ok, redact, validate_id_card

VALID_ID = "11010519491231002X"


def test_id_card_checksum():
    assert validate_id_card(VALID_ID)
    assert not validate_id_card(VALID_ID[:-1] + "1")


def test_id_card_impossible_date():
    assert not validate_id_card("11010519491332002X")


def test_luhn():
    assert luhn_ok("4111111111111111")
    assert not luhn_ok("4111111111111112")


def test_phone_not_overmatched():
    assert detect("电话 13812345678")[0][0] == "phone"
    assert detect("订单号 138123456789") == []  # 12 位，不是手机号


def test_email_and_phone_together():
    kinds = [hit[0] for hit in detect("联系 13812345678 或 a.b@example.com")]
    assert kinds == ["phone", "email"]


def test_order_number_is_not_bank_card():
    # 16 位但 Luhn 不过，必须被挡住
    assert detect("订单 1234567890123456") == []


def test_redact_keeps_head_and_tail():
    masked, types = redact("身份证 11010519491231002X 请核对")
    assert types == ["id_card"]
    assert "11010519491231002X" not in masked
    assert masked.startswith("身份证 110")
    assert masked.endswith("002X 请核对")
    assert "*" in masked


def test_redact_email():
    masked, types = redact("邮箱 a.b@example.com")
    assert types == ["email"]
    assert masked.endswith("a***@example.com")
