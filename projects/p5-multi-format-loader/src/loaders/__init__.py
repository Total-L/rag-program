"""P5 loaders —— 按后缀分发的多格式入口。

公共 API：
    from src.loaders import load
    doc = load(Path("foo.pdf"))   # 自动按后缀分到对应 loader
"""

from __future__ import annotations

from pathlib import Path

from ..models import Document, ExtractionError, SourceFormat
from .docx import load as load_docx
from .html import load as load_html
from .pdf import load as load_pdf
from .xlsx import load as load_xlsx

_LOADERS = {
    ".pdf": (load_pdf, SourceFormat.PDF),
    ".docx": (load_docx, SourceFormat.DOCX),
    ".xlsx": (load_xlsx, SourceFormat.XLSX),
    ".html": (load_html, SourceFormat.HTML),
    ".htm": (load_html, SourceFormat.HTML),
}


def load(path: Path | str) -> Document:
    """按后缀分发。"""
    p = Path(path)
    fmt = _LOADERS.get(p.suffix.lower())
    if fmt is None:
        raise ExtractionError(f"unsupported format: {p.suffix} ({p.name})")
    loader, _ = fmt
    return loader(p)


__all__ = ["load", "Document", "ExtractionError", "SourceFormat"]
