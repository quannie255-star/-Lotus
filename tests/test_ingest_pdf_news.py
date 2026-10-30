"""ingest/pdf.py + ingest/news.py + parse/pdf_extractor.py 验收测试（M3）"""

import json
from pathlib import Path

import pyarrow.parquet as pq
import pymupdf
import pytest

from common import ids
from common.first_seen import FirstSeenLedger
from ingest import news, pdf

CLOCK = "2026-10-30T00:00:00+00:00"


@pytest.fixture
def ledger(tmp_path):
    return FirstSeenLedger(tmp_path / "fs.json", clock=lambda: CLOCK)


# ---------------------------------------------------------------- pdf 提取

def _make_pdf(path: Path, pages: list) -> Path:
    """生成测试 PDF。insert_textbox 自动折行——insert_text 不折行，长句会跑出页面被裁，
    每页抽出字符数骤降，恰好触发「疑似扫描页」误报（M3 实现期实测教训）。"""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 545, 790), text,
                            fontname="china-s", fontsize=12)
    doc.save(path)
    doc.close()
    return path


def test_pdf_textlayer_extraction(tmp_path):
    from parse.pdf_extractor import extract_pdf

    p = _make_pdf(tmp_path / "good.pdf", ["政府工作报告摘要：全年经济数据综述。", "第二页：民生与就业数据。"])
    result = extract_pdf(p)
    assert not result.empty
    assert "经济数据" in result.text
    assert result.page_count == 2
    assert result.warnings == []


def test_pdf_scanned_page_warns(tmp_path):
    from parse.pdf_extractor import extract_pdf

    p = _make_pdf(tmp_path / "scan.pdf", [""])  # 空文本页 ≈ 扫描页
    result = extract_pdf(p)
    assert result.empty
    assert any("疑似扫描" in w for w in result.warnings)


# ---------------------------------------------------------------- pdf 适配器

def test_pdf_run_roundtrip(tmp_path, ledger, monkeypatch):
    # monkeypatch ledger 与归档目录到 tmp（归档件不污染仓库 data/）
    monkeypatch.setattr(pdf, "RAW_ARCHIVE", tmp_path / "archive")
    good = _make_pdf(tmp_path / "g.pdf", ["白皮书正文第一页，包含足够文字。"])
    entries = [{"name": "g.pdf", "path": str(good)}]

    kwargs = dict(output_dir=tmp_path / "raw", ledger_path=tmp_path / "fs.json",
                  runs_path=tmp_path / "runs.jsonl")
    first = pdf.run(entries=entries, **kwargs)
    assert first["cached"] is False and first["rows"] == 1
    part = first["output_dir"] / "part-00000.parquet"
    table = pq.read_table(part).to_pylist()
    digest_srcid = table[0]["source_id"]
    assert table[0]["doc_id"] == ids.spine_doc_id("pdf", digest_srcid)
    assert table[0]["raw_uri"].endswith(".pdf")
    assert (tmp_path / "archive" / f"{digest_srcid}.pdf").exists()  # 原始字节归档

    second = pdf.run(entries=entries, **kwargs)
    assert second["cached"] is True and second["rows"] is None


def test_pdf_guardrail_and_quarantine(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "RAW_ARCHIVE", tmp_path / "archive")
    blank = _make_pdf(tmp_path / "blank.pdf", [""])
    entries = [{"name": "blank.pdf", "path": str(blank)}]
    with pytest.raises(ValueError, match="护栏"):
        pdf.run(entries=entries, output_dir=tmp_path / "raw",
                ledger_path=tmp_path / "fs.json", runs_path=tmp_path / "runs.jsonl")


def test_pdf_magic_check(tmp_path):
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"<html>not a pdf</html>")
    with pytest.raises(ValueError, match="不是 PDF"):
        pdf.fetch_bytes({"name": "fake.pdf", "path": str(fake)})


def test_pdf_entries_fingerprint_isolation(tmp_path, monkeypatch):
    """同 output_dir 下不同 entries 不得假缓存（M3 自查发现的陷阱的回归测试）。"""
    monkeypatch.setattr(pdf, "RAW_ARCHIVE", tmp_path / "archive")
    a = _make_pdf(tmp_path / "a.pdf", ["文档甲的内容。"])
    b = _make_pdf(tmp_path / "b.pdf", ["文档乙的内容。"])
    kwargs = dict(output_dir=tmp_path / "raw", ledger_path=tmp_path / "fs.json",
                  runs_path=tmp_path / "runs.jsonl")
    pdf.run(entries=[{"name": "a.pdf", "path": str(a)}], **kwargs)
    second = pdf.run(entries=[{"name": "b.pdf", "path": str(b)}], **kwargs)
    assert second["cached"] is False  # 清单变了必须重算


# ---------------------------------------------------------------- news

def test_news_golden_and_idempotent(tmp_path):
    src = news.write_mock_lines(tmp_path / "news.jsonl", n=5, seed=3)
    led = FirstSeenLedger(tmp_path / "fs.json", clock=lambda: CLOCK)
    docs = list(news.iter_docs(src, led))
    assert len(docs) == 5
    d = docs[0]
    assert d["source"] == "news"
    assert d["source_timestamp"].startswith("2026-")
    assert d["timestamp_source"] == "meta"
    assert d["genre"] == "news"
    assert d["source_metadata"]["publisher"] in {"新华源", "人民源", "澎湃源"}

    kwargs = dict(data_file=src, output_dir=tmp_path / "raw",
                  ledger_path=tmp_path / "fs.json", runs_path=tmp_path / "runs.jsonl")
    first = news.run(**kwargs)
    assert first["cached"] is False and first["rows"] == 5
    assert news.run(**kwargs)["cached"] is True


def test_news_fail_fast_on_missing_id(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"content": "x"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="缺 id"):
        list(news.iter_docs(bad, FirstSeenLedger(tmp_path / "fs.json")))
