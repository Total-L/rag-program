"""P5 loader 单测。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src.loaders import load  # noqa: E402
from src.models import BlockType  # noqa: E402


def _has_paddleocr() -> bool:
    try:
        import paddleocr  # noqa: F401

        return True
    except ImportError:
        return False


def test_pdf_loader():
    doc = load(HERE.parent / "fixtures" / "sample.pdf")
    assert doc.source_format.value == "pdf"
    assert any(b.block_type == BlockType.TEXT for b in doc.blocks)
    tables = doc.by_type(BlockType.TABLE)
    assert len(tables) == 1
    assert "Region" in tables[0].headers
    assert "Revenue" in tables[0].text
    print("  ✓ pdf loader")


def test_docx_loader():
    doc = load(HERE.parent / "fixtures" / "sample.docx")
    assert doc.source_format.value == "docx"
    headings = doc.by_type(BlockType.HEADING)
    assert any("Week" in h.text for h in headings)
    tables = doc.by_type(BlockType.TABLE)
    assert len(tables) == 1
    assert "Day" in tables[0].headers
    print("  ✓ docx loader")


def test_xlsx_loader():
    doc = load(HERE.parent / "fixtures" / "sample.xlsx")
    assert doc.source_format.value == "xlsx"
    tables = doc.by_type(BlockType.TABLE)
    assert len(tables) == 2  # Headcount + Budget
    sheet_names = {b.page_or_sheet for b in tables}
    assert "Headcount" in sheet_names and "Budget" in sheet_names
    print("  ✓ xlsx loader")


def test_html_loader():
    doc = load(HERE.parent / "fixtures" / "sample.html")
    assert doc.source_format.value == "html"
    tables = doc.by_type(BlockType.TABLE)
    assert len(tables) == 1
    images = doc.by_type(BlockType.IMAGE)
    assert len(images) == 1
    assert "Laptop" in images[0].text
    print("  ✓ html loader")


def test_table_markdown_roundtrip():
    """表格 markdown 格式正确（含表头分隔行）。"""
    doc = load(HERE.parent / "fixtures" / "sample.pdf")
    tbl = doc.by_type(BlockType.TABLE)[0]
    lines = tbl.text.split("\n")
    assert lines[0].startswith("| Region |")
    assert lines[1].startswith("|---")
    print("  ✓ table markdown format")


@pytest.mark.skipif(not _has_paddleocr(), reason="需要 paddleocr（重模型，CI 默认跳过）")
def test_pdf_image_ocr():
    """PDF 图片必须真 OCR —— 不能再是 bbox 占位。"""
    from src.ocr import reset_cache

    reset_cache()  # 确保 OCR 真跑
    doc = load(HERE.parent / "fixtures" / "sample.pdf")
    images = doc.by_type(BlockType.IMAGE)
    assert len(images) >= 1, "PDF 应至少含 1 张图"
    # OCR 抽出 APAC/Growth/Revenue 等关键词（tesseract 真实识别）
    combined = " ".join(b.text for b in images).lower()
    keywords = ["apac", "growth", "revenue"]
    hits = [k for k in keywords if k in combined]
    assert hits, f"OCR 应识别关键词 {keywords}，实际: {combined[:200]!r}"
    print(f"  ✓ pdf image OCR ({len(images)} imgs, hits={hits})")


@pytest.mark.skipif(not _has_paddleocr(), reason="需要 paddleocr（重模型，CI 默认跳过）")
def test_html_image_ocr():
    """HTML 图片必须真 OCR：识别 alt 之外的图内文字（如产品价格）。"""
    from src.ocr import reset_cache

    reset_cache()
    doc = load(HERE.parent / "fixtures" / "sample.html")
    images = doc.by_type(BlockType.IMAGE)
    assert len(images) == 1
    img = images[0]
    assert "1499" in img.text, f"HTML 图应被 OCR 识别价格，实际: {img.text[:200]!r}"
    assert img.ocr_text, "ocr_text 字段必须非空"
    print(f"  ✓ html image OCR (ocr_text len={len(img.ocr_text)})")


if __name__ == "__main__":
    tests = [
        test_pdf_loader,
        test_docx_loader,
        test_xlsx_loader,
        test_html_loader,
        test_table_markdown_roundtrip,
        test_pdf_image_ocr,
        test_html_image_ocr,
    ]
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  ✗ {t.__name__}: {e}")
            sys.exit(1)
    print(f"\n{len(tests)}/{len(tests)} passed")
