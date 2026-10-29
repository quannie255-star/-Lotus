"""common/first_seen.py 的测试：首次入库时间必须"落一次盘、重跑沿用"。"""

import json

import pytest

from common.first_seen import FirstSeenLedger


def test_same_doc_gets_same_timestamp_within_a_ledger(tmp_path):
    ticks = iter(["2026-10-29T10:00:00+00:00", "2026-10-29T11:00:00+00:00"])
    ledger = FirstSeenLedger(tmp_path / "l.json", clock=lambda: next(ticks))

    assert ledger.resolve("doc-a") == "2026-10-29T10:00:00+00:00"
    assert ledger.resolve("doc-a") == "2026-10-29T10:00:00+00:00"  # 不推进时钟
    assert ledger.resolve("doc-b") == "2026-10-29T11:00:00+00:00"


def test_commit_persists_and_reload_keeps_old_values(tmp_path):
    path = tmp_path / "l.json"
    ledger = FirstSeenLedger(path, clock=lambda: "2026-10-29T10:00:00+00:00")
    ledger.resolve("doc-a")
    assert ledger.commit() is True

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored == {"doc-a": "2026-10-29T10:00:00+00:00"}

    # 新进程用新时钟读同一本台账：旧 doc 沿用旧值，新 doc 才拿新时钟
    reloaded = FirstSeenLedger(path, clock=lambda: "2030-01-01T00:00:00+00:00")
    assert reloaded.resolve("doc-a") == "2026-10-29T10:00:00+00:00"
    assert reloaded.resolve("doc-b") == "2030-01-01T00:00:00+00:00"


def test_nothing_pending_means_no_write(tmp_path):
    path = tmp_path / "l.json"
    ledger = FirstSeenLedger(path, clock=lambda: "2026-10-29T10:00:00+00:00")
    ledger.resolve("doc-a")
    ledger.commit()
    before = path.read_bytes()

    assert ledger.commit() is False  # 重跑零写入
    assert path.read_bytes() == before


def test_failed_run_does_not_persist_assignments(tmp_path):
    """transform 中途失败时台账不该被提交——数据没发布，就没有"首次入库"。"""
    path = tmp_path / "l.json"
    ledger = FirstSeenLedger(path, clock=lambda: "2026-10-29T10:00:00+00:00")
    ledger.resolve("doc-a")
    # 模拟没走到 commit 就抛异常
    assert not path.exists()


def test_broken_ledger_raises_instead_of_overwriting(tmp_path):
    path = tmp_path / "l.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        FirstSeenLedger(path)

    path.write_text('["doc-a"]', encoding="utf-8")
    with pytest.raises(ValueError):
        FirstSeenLedger(path)

    path.write_text('{"doc-a": 123}', encoding="utf-8")
    with pytest.raises(ValueError):
        FirstSeenLedger(path)
