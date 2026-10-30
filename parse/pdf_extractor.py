"""parse/pdf_extractor.py — PDF → 正文提取（M3，文本层轻路径）（DEVLOG M3-D1）

分层策略（WP-01 N3 决策的落地）：
- 文本层完好的 PDF（电子版，绝大多数）→ PyMuPDF 直接抽取，秒级、零重依赖；
- 疑似扫描件（每页抽出字符数过低）→ 不硬造内容，返回 warnings 并由调用方 quarantine——
  OCR 是成本悬崖（MinerU/torch），只对确实需要的子集启用（N3 升级路径，见 MANUAL）。

输出刻意是 plain_text 而非 markdown：PyMuPDF 给的是线性文本流，硬造标题层级是撒谎。
MinerU 升级后切 markdown 并同步 registry 的 content_format（事实必须与声明一致）。
"""

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

ENGINE_VERSION = "pymupdf-1.28.2-textlayer"
# 每页低于该字符数视为「疑似扫描页」。扫描页实际接近 0-5 字符；正文页（含很短的目录页）
# 一般两位数起——阈值 10 是分隔带（20 会把 19 字的短目录页误伤，M3 实测校准）
SCANNED_PAGE_CHAR_THRESHOLD = 10


@dataclass
class ExtractResult:
    """提取结果。失败不抛异常——warnings 承载原因，处置权在调用方（quarantine 与否）。

    字段: text — 抽取正文（可能为空串）；page_count — 页数；engine_version — 引擎标识；
          warnings — 非致命问题清单（如疑似扫描页号）；empty — text 是否为空（便捷位）
    """

    text: str
    page_count: int
    engine_version: str
    warnings: list = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.text.strip()


def extract_pdf(pdf_path) -> ExtractResult:
    """提取 PDF 文本层。

    参数: pdf_path — PDF 文件路径
    返回: ExtractResult（见 dataclass；文件损坏时 text=""，warnings 含原因）
    异常: 不抛（pymupdf 的异常被捕获进 warnings——坏 PDF 是数据问题，走 quarantine，不是程序错误）
    幂等: 纯函数，同文件同输出
    """
    path = Path(pdf_path)
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001 — 坏文件是数据问题
        return ExtractResult(text="", page_count=0, engine_version=ENGINE_VERSION,
                             warnings=[f"open_failed: {type(exc).__name__}: {exc}"])
    try:
        pages, warnings = [], []
        for index, page in enumerate(doc, 1):
            text = page.get_text("text").strip()
            if len(text) < SCANNED_PAGE_CHAR_THRESHOLD:
                warnings.append(f"page {index}: 疑似扫描/图像页（{len(text)} 字符）")
            pages.append(text)
        return ExtractResult(
            text="\n\n".join(p for p in pages if p),
            page_count=doc.page_count,
            engine_version=ENGINE_VERSION,
            warnings=warnings,
        )
    finally:
        doc.close()
