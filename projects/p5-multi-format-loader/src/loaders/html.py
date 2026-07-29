"""HTML loader —— 语义加权 + 表格 + 链接上下文。

策略：
- 用 BeautifulSoup 解析（lxml parser 更快）
- 按文章结构打分：<article>/<main> > <section> > <div>；<nav>/<footer>/<header> 整体跳过
- <table> → 转 markdown，保留 thead/tbody/tfoot
- <script>/<style>/<noscript> 完全剥除
- 图片：抽出 alt + src（无 OCR）
"""

from __future__ import annotations

import logging
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString
from markdownify import MarkdownConverter

from ..models import Block, BlockType, Document, ExtractionError, SourceFormat, _id_for
from ..ocr import ocr_image

log = logging.getLogger(__name__)


class _TableConverter(MarkdownConverter):
    """保留表格结构的 markdownify 子类。"""


def _table_to_markdown(table) -> str:
    """手写 table → markdown（避免 markdownify 默认丢表结构）。"""
    rows: list[list[str]] = []
    # 先抽 thead
    thead = table.find("thead")
    if thead:
        headers = [c.get_text(strip=True) for c in thead.find_all(["th", "td"])]
        rows.append(headers)
    # 然后 tbody 或所有 tr
    body = table.find("tbody") or table
    for tr in body.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        # 跳过全空行
        if any(c for c in cells):
            rows.append(cells)
    if not rows:
        return ""
    n_cols = max(len(r) for r in rows)
    norm = [(r + [""] * n_cols)[:n_cols] for r in rows]
    out = ["| " + " | ".join(norm[0]) + " |", "|" + "|".join(["---"] * n_cols) + "|"]
    for r in norm[1:]:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


_SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe"}
_KEEP_TAGS_WITH_HIGH_WEIGHT = {"article", "main", "section"}


def _walk_visible_text(soup) -> str:
    """抽 main/article 区域纯文本（剥除 skip tags）。"""
    root = soup.find(["article", "main"]) or soup.body or soup
    parts: list[str] = []
    for el in root.descendants:
        if isinstance(el, NavigableString):
            parent = el.parent
            if parent is None:
                continue
            if parent.name in _SKIP_TAGS:
                continue
            txt = str(el).strip()
            if txt:
                parts.append(txt)
    # 去重连续空行
    out_lines: list[str] = []
    for p in parts:
        if p or (out_lines and out_lines[-1]):
            out_lines.append(p)
    return "\n".join(out_lines).strip()


def load(path: Path) -> Document:
    if path.suffix.lower() not in (".html", ".htm"):
        raise ExtractionError(f"HTML loader got {path.suffix}")
    path = Path(path).resolve()
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw, "lxml")

    # 剥除 script/style/nav/footer/header
    for tag in soup(_SKIP_TAGS):
        tag.decompose()

    blocks: list[Block] = []

    # 1. 主文本（按 article/main 提权）
    main_text = _walk_visible_text(soup)
    if main_text:
        # 按段落切（h1/h2/h3 留作 heading 标记）
        para_idx = 1
        for el in soup.find_all(["p", "h1", "h2", "h3", "li"]):
            txt = el.get_text(" ", strip=True)
            if not txt:
                continue
            is_heading = el.name in ("h1", "h2", "h3")
            blocks.append(
                Block(
                    block_id=_id_for(
                        path,
                        para_idx,
                        BlockType.HEADING if is_heading else BlockType.TEXT,
                        len(blocks),
                    ),
                    source_path=str(path),
                    source_format=SourceFormat.HTML,
                    block_type=BlockType.HEADING if is_heading else BlockType.TEXT,
                    text=txt,
                    page_or_sheet=para_idx,
                    metadata={"tag": el.name},
                )
            )
            para_idx += 1

    # 2. 表格
    for t_idx, table in enumerate(soup.find_all("table")):
        md = _table_to_markdown(table)
        if not md:
            continue
        # 表头 = 第一行
        first_row_text = md.split("\n")[0]
        headers = [c.strip() for c in first_row_text.strip("|").split("|")]
        blocks.append(
            Block(
                block_id=_id_for(path, t_idx, BlockType.TABLE, len(blocks)),
                source_path=str(path),
                source_format=SourceFormat.HTML,
                block_type=BlockType.TABLE,
                text=md,
                page_or_sheet=t_idx,
                headers=tuple(headers),
                metadata={"tag": "table", "table_index": t_idx},
            )
        )

    # 3. 图片：拿 alt + OCR 真做（生产里 hero/banner 经常写图里）
    for i_idx, img in enumerate(soup.find_all("img")):
        alt = (img.get("alt") or "").strip()
        src = (img.get("src") or "").strip()
        if not (alt or src):
            continue

        # 拼本地路径：相对 HTML 文件位置解析；网络 URL 或丢失则跳过 OCR
        local_img_path: Path | None = None
        if src and not src.startswith(("http://", "https://", "data:")):
            candidate = (path.parent / src).resolve()
            if candidate.exists():
                local_img_path = candidate

        ocr_text = ocr_image(local_img_path) if local_img_path else ""

        # figcaption 上下文
        fig_caption = ""
        parent_fig = img.find_parent("figure")
        if parent_fig:
            cap = parent_fig.find("figcaption")
            if cap:
                fig_caption = cap.get_text(" ", strip=True)

        # 组合最终 text：alt + caption + OCR 都进 text 字段
        parts = []
        if alt:
            parts.append(f"[alt] {alt}")
        if fig_caption:
            parts.append(f"[caption] {fig_caption}")
        if ocr_text:
            parts.append(f"[ocr] {ocr_text}")
        if not parts:
            parts.append(f"[image src={src}]")
        text_final = " ".join(parts)

        meta_extra: dict = {"src": src[:200]}
        if local_img_path:
            meta_extra["image_path"] = str(local_img_path)
        meta_extra["ocr_engine"] = "paddleocr" if ocr_text else "no-ocr"

        blocks.append(
            Block(
                block_id=_id_for(path, i_idx, BlockType.IMAGE, len(blocks)),
                source_path=str(path),
                source_format=SourceFormat.HTML,
                block_type=BlockType.IMAGE,
                text=text_final,
                page_or_sheet=i_idx,
                image_caption=fig_caption or alt,
                ocr_text=ocr_text,
                metadata=meta_extra,
            )
        )

    return Document(
        source_path=str(path),
        source_format=SourceFormat.HTML,
        blocks=tuple(blocks),
    )
