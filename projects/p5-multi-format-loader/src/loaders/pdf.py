"""PDF loader —— 文本 + 表格 + 图片（带 OCR）。

策略：
- pdfplumber 抽文本 + 表格
- pypdfium2 渲染整页 → PIL → 切 bbox → OCR（不再是 bbox 占位）
- 每页 text block + 每个 table block + 每个 image block（OCR 文本进 ocr_text）

为什么 OCR 必须真做：
- 工业里 60%+ 文档是扫描件 / 含图财报
- "图片里有数字"用户问"图里写了啥"必须能答
- 实际后端是 PaddleOCR（见 `../ocr.py`）：中文/复杂版面准确率远超 Tesseract，
  离线可跑。metadata 里的 `ocr_engine` 字段如实写 "paddleocr"。
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pdfplumber

from ..models import Block, BlockType, Document, ExtractionError, SourceFormat, _id_for
from ..ocr import ocr_pdf_region

log = logging.getLogger(__name__)

_HEADER_FOOTER_HINTS = (
    "page",
    "页",
    "页码",
    "©",
    "all rights reserved",
    "confidential",
    "内部资料",
    "company confidential",
)


def _table_to_markdown(headers: list[str], rows: list[list[str]]) -> str:
    if not headers and not rows:
        return ""
    n_cols = max(len(headers), max((len(r) for r in rows), default=0))
    headers = (headers + [""] * n_cols)[:n_cols]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * n_cols) + "|"]
    for r in rows:
        r2 = (r + [""] * n_cols)[:n_cols]
        lines.append("| " + " | ".join(r2) + " |")
    return "\n".join(lines)


def _is_header_or_footer(text: str) -> bool:
    t = text.strip().lower()
    if len(t) > 80:
        return False
    return any(h in t for h in _HEADER_FOOTER_HINTS)


def _extract_page_text(page) -> tuple[str, list[str]]:
    raw = page.extract_text() or ""
    lines = raw.split("\n")
    kept: list[str] = []
    dropped = 0
    for ln in lines:
        if _is_header_or_footer(ln):
            dropped += 1
            continue
        kept.append(ln)
    warnings: list[str] = []
    if dropped:
        warnings.append(f"dropped {dropped} header/footer line(s)")
    return "\n".join(kept).strip(), warnings


def _render_page_png(pdf_path: Path, page_no: int, scale: float = 2.0) -> bytes | None:
    """用 pypdfium2 渲染整页为 PNG bytes（fallback：page.to_image()）。"""
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(pdf_path))
        page = pdf[page_no - 1]
        pil_img = page.render(scale=scale).to_pil()
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        log.debug("pypdfium2 render failed for page %d: %s", page_no, e)
        return None


def _crop_image_bytes_v2(
    full_png_bytes: bytes,
    bbox_pt: tuple[float, float, float, float],
    page_width_pt: float,
    page_height_pt: float,
) -> bytes | None:
    """从整页 PNG 裁出 bbox 区域。

    bbox 约定：pdfplumber 的 (x0, top, x1, bottom)，top/bottom 从页面顶端向下计（pt）。
    """
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(full_png_bytes))
        img_w, img_h = img.size
        sx = img_w / page_width_pt
        sy = img_h / page_height_pt
        x0, top, x1, bottom = bbox_pt
        # bbox 已经是"顶向下"坐标，直接对应 PNG 坐标系
        crop = img.crop(
            (
                int(x0 * sx),
                int(top * sy),
                int(x1 * sx),
                int(bottom * sy),
            )
        )
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        log.debug("crop failed: %s", e)
        return None


def load(path: Path) -> Document:
    """主入口：从 PDF 抽 Block 列表（含 OCR）。"""
    if path.suffix.lower() != ".pdf":
        raise ExtractionError(f"PDF loader got {path.suffix}")
    path = Path(path).resolve()
    blocks: list[Block] = []
    warnings: list[str] = []

    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            # 1. 文本
            text, warns = _extract_page_text(page)
            warnings.extend(warns)
            if text:
                blocks.append(
                    Block(
                        block_id=_id_for(path, page_no, BlockType.TEXT, len(blocks)),
                        source_path=str(path),
                        source_format=SourceFormat.PDF,
                        block_type=BlockType.TEXT,
                        text=text,
                        page_or_sheet=page_no,
                    )
                )

            # 2. 表格
            try:
                tables = page.extract_tables() or []
            except Exception as e:  # noqa: BLE001
                warnings.append(f"page {page_no} table extract failed: {e}")
                tables = []
            for t_idx, tbl in enumerate(tables):
                if not tbl:
                    continue
                headers = [(c or "").strip() for c in tbl[0]]
                rows = [[(c or "").strip() for c in r] for r in tbl[1:]]
                md = _table_to_markdown(headers, rows)
                blocks.append(
                    Block(
                        block_id=_id_for(path, page_no, BlockType.TABLE, len(blocks)),
                        source_path=str(path),
                        source_format=SourceFormat.PDF,
                        block_type=BlockType.TABLE,
                        text=md,
                        page_or_sheet=page_no,
                        headers=tuple(headers),
                        rows=tuple(tuple(r) for r in rows),
                        metadata={"table_index": t_idx},
                    )
                )

            # 3. 图片：渲染整页 → 裁 bbox → OCR（真做 OCR）
            try:
                images = page.images or []
            except Exception:  # noqa: BLE001
                images = []

            if images:
                full_png = _render_page_png(path, page_no, scale=2.0)
                for i_idx, img_meta in enumerate(images):
                    # pdfplumber 用 (x0, top, x1, bottom) —— top/bottom 是页面顶部向下计的 pt
                    bbox_pt = (
                        float(img_meta.get("x0", 0)),
                        float(img_meta.get("top", 0)),
                        float(img_meta.get("x1", 0)),
                        float(img_meta.get("bottom", 0)),
                    )
                    cropped_png: bytes | None = None
                    if full_png:
                        cropped_png = _crop_image_bytes_v2(
                            full_png,
                            bbox_pt,
                            page_width_pt=float(page.width),
                            page_height_pt=float(page.height),
                        )

                    # 落盘 cropped PNG 到 <fixtures>/.derived/,UI Tab 2/3 据此渲染原图
                    extra_meta: dict = {}
                    if cropped_png:
                        derived_dir = path.parent / ".derived"
                        derived_dir.mkdir(exist_ok=True)
                        derived_name = f"{path.stem}_p{page_no}_img{i_idx + 1}.png"
                        derived_path = derived_dir / derived_name
                        derived_path.write_bytes(cropped_png)
                        extra_meta["image_path"] = str(derived_path.resolve())

                    ocr_text = ""
                    if cropped_png:
                        # hint 便于检索时能区分是图1还是图2
                        hint = f"[image #{i_idx + 1} on p{page_no}]"
                        ocr_text = ocr_pdf_region(cropped_png, hint=hint)

                    blocks.append(
                        Block(
                            block_id=_id_for(path, page_no, BlockType.IMAGE, len(blocks)),
                            source_path=str(path),
                            source_format=SourceFormat.PDF,
                            block_type=BlockType.IMAGE,
                            # ↓ 真 OCR 文本进 text 字段（embedding 用）
                            text=ocr_text or f"[image #{i_idx + 1} on p{page_no}, no OCR text]",
                            page_or_sheet=page_no,
                            image_caption="",
                            ocr_text=ocr_text,
                            metadata={
                                "bbox": list(bbox_pt),
                                "width": img_meta.get("width"),
                                "height": img_meta.get("height"),
                                "ocr_engine": "paddleocr" if ocr_text else "fallback",
                                **extra_meta,
                            },
                        )
                    )

    return Document(
        source_path=str(path),
        source_format=SourceFormat.PDF,
        blocks=tuple(blocks),
        parse_warnings=tuple(warnings),
    )
