"""P5 — 生成 fixtures/architecture.pdf (3 页) 用作复杂系统架构图 fixture。

为什么这样设计:
- p1 主架构图必须用 PIL 画 PNG 嵌入 → pdfplumber 看到 image block → 触发 PaddleOCR
  (如果 p1 走 reportlab 矢量 rect,不会被 pdfplumber 识别为 image,OCR 不跑)
- p2/p3 用 reportlab 矢量绘制 → 走 page.extract_text() 直接拿文本,无需 OCR
- 关键词在 p1 (OCR 命中) 和 p3 (PDF 文本) 都有冗余,QA 命中率高

输出:
    fixtures/architecture.pdf  (3 页)
    p1: image block — 5 层架构 + 横切
    p2: table block — 组件责任表
    p3: text  block — 数据流文字描述

跑:
    ./.venv/bin/python scripts/make_arch_pdf.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Table, TableStyle

HERE = Path(__file__).resolve().parents[1]
OUTPUT = HERE / "fixtures" / "architecture.pdf"
TMP_P1 = Path("/tmp/arch_p1.png")
OUTPUT.parent.mkdir(exist_ok=True)

# macOS Helvetica .ttc(TrueType Collection,index 0=Regular, 1=Bold)
FONT_PATH = "/System/Library/Fonts/Helvetica.ttc"

# ── 调色板 ─────────────────────────────────────────────────
LAYER_BG = "#EEF4FF"
LAYER_BORDER = "#1E3A8A"
NODE_BG = "#FFFFFF"
NODE_BORDER = "#1E3A8A"
SUB_BG = "#F3F4F6"
SUB_BORDER = "#6B7280"
ARROW = "#374151"
TEXT_DARK = "#111827"
TEXT_MUTED = "#4B5563"
CROSSCUT_BG = "#FEF3C7"
CROSSCUT_BORDER = "#D97706"

PAGE_W, PAGE_H = landscape(A4)  # 842 x 595 pt


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size, index=1 if bold else 0)


def _text_w(draw, txt: str, font) -> float:
    """PIL 文字宽度精确测量。"""
    bbox = draw.textbbox((0, 0), txt, font=font)
    return bbox[2] - bbox[0]


def _text_h(font) -> float:
    bbox = font.getbbox("Hg")
    return bbox[3] - bbox[1]


def _draw_arrow(d, x1, y1, x2, y2, color=ARROW, lw=4):
    d.line([(x1, y1), (x2, y2)], fill=color, width=lw)
    angle = math.atan2(y2 - y1, x2 - x1)
    # arrow head(三个点)
    ah = 22
    aw = 14
    p1 = (x2, y2)
    p2 = (
        x2 - ah * math.cos(angle) + aw * math.sin(angle),
        y2 - ah * math.sin(angle) - aw * math.cos(angle),
    )
    p3 = (
        x2 - ah * math.cos(angle) - aw * math.sin(angle),
        y2 - ah * math.sin(angle) + aw * math.cos(angle),
    )
    d.polygon([p1, p2, p3], fill=color)


def _draw_node(d, x, y, w, h, label, size=42, bold=True, fill=NODE_BG, border=NODE_BORDER):
    """画矩形节点框 + 居中文字。"""
    d.rectangle([(x, y), (x + w, y + h)], fill=fill, outline=border, width=3)
    f = _font(size, bold)
    tw = _text_w(d, label, f)
    th = _text_h(f)
    tx = x + (w - tw) / 2
    ty = y + (h - th) / 2 - 4
    d.text((tx, ty), label, fill=TEXT_DARK, font=f)


def _draw_layer(d, x, y, w, h, label, sub, mains, subs, font_main_size=42):
    """画一层架构:左 label + 中 main 节点 + main 下 sub 节点。"""
    # layer 背景框
    d.rectangle([(x, y), (x + w, y + h)], fill=LAYER_BG, outline=LAYER_BORDER, width=3)

    # 左 label
    f_label = _font(54, True)
    f_sub = _font(34, False)
    d.text((x + 18, y + 22), label, fill=TEXT_DARK, font=f_label)
    d.text((x + 18, y + 92), sub, fill=TEXT_MUTED, font=f_sub)

    # main 节点
    label_w = 280  # 留给左侧 label 的宽度(px)
    nodes_x0 = x + label_w
    nodes_w = w - label_w - 30
    n = len(mains)
    gap = 24
    main_w = (nodes_w - gap * (n - 1)) / n
    main_h = 80
    # 节点 y_main:层垂直居中偏上,留 sub 位置
    y_main = y + h - main_h - 40
    centers = []
    for j, nm in enumerate(mains):
        nx0 = nodes_x0 + j * (main_w + gap)
        _draw_node(d, nx0, y_main, main_w, main_h, nm, size=font_main_size)
        centers.append((nx0 + main_w / 2, y_main + main_h / 2))

        if subs.get(nm):
            sn = subs[nm]
            sn_count = len(sn)
            sub_w_total = main_w - 24
            sgap = 10
            sw = (sub_w_total - sgap * (sn_count - 1)) / sn_count
            sh = 50
            for k, sub_label in enumerate(sn):
                sx0 = nx0 + 12 + k * (sw + sgap)
                sy0 = y_main - sh - 12
                d.rectangle(
                    [(sx0, sy0), (sx0 + sw, sy0 + sh)], fill=SUB_BG, outline=SUB_BORDER, width=2
                )
                f_sub_n = _font(28, False)
                stw = _text_w(d, sub_label, f_sub_n)
                sth = _text_h(f_sub_n)
                d.text(
                    (sx0 + (sw - stw) / 2, sy0 + (sh - sth) / 2 - 4),
                    sub_label,
                    fill=TEXT_DARK,
                    font=f_sub_n,
                )
    return centers


# ── p1 主架构图(PIL 画 PNG) ─────────────────────────────────
def draw_p1_png(path: Path):
    """画 2200 x 1800 px 主架构图。

    布局(顶部到底部):
        y=0-180:   title + subtitle
        y=180-380: Client
        y=400-600: Edge
        y=620-840: API Gateway(含 sub)
        y=860-1100: Service Layer(含 sub)
        y=1120-1320: Storage
        y=1340-1500: Cross-cutting
        y=1720-1760: caption
    """
    w, h = 2200, 1800
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)

    # 标题
    f_title = _font(72, True)
    f_subtitle = _font(38, False)
    d.text((60, 30), "RAG Platform — System Architecture v1", fill=TEXT_DARK, font=f_title)
    d.text(
        (60, 130),
        "5-Layer stack: Client / Edge / API Gateway / Service Layer / Storage + Cross-cutting Observability",
        fill=TEXT_MUTED,
        font=f_subtitle,
    )

    margin_x = 60
    layer_w = w - margin_x * 2
    # (yt_top, h, label, sub_label, mains, subs)  —— yt_top 是该层顶端 y 坐标
    layers = [
        (
            180,
            200,
            "Client",
            "Web / mobile / CLI",
            ["Streamlit Web UI", "Mobile App iOS Android", "CLI curl"],
            {},
        ),
        (
            400,
            200,
            "Edge",
            "TLS / CDN",
            ["Nginx reverse proxy", "CDN Cloudflare", "TLS terminator"],
            {},
        ),
        (
            620,
            220,
            "API Gateway",
            "FastAPI single entry",
            ["FastAPI Gateway"],
            {"FastAPI Gateway": ["Auth JWT RS256", "Rate Limiter token-bucket", "Request Router"]},
        ),
        (
            860,
            240,
            "Service Layer",
            "3 core services",
            ["Ingestion", "Retrieval", "Generation"],
            {
                "Ingestion": ["Docling parser", "Recursive chunker", "Ollama Embedder"],
                "Retrieval": ["BM25 dense", "Chroma vector DB", "Cross-Enc reranker"],
                "Generation": ["Llama 3 LLM", "Grounded prompt cite", "Streaming validate"],
            },
        ),
        (
            1120,
            200,
            "Storage",
            "5 persistent stores",
            [
                "Vector DB Chroma",
                "Cache Redis",
                "Postgres metadata",
                "BM25 Index Elastic",
                "Object Store S3",
            ],
            {},
        ),
    ]

    layer_centers = []
    for yt, h, label, sub, mains, subs in layers:
        # _draw_layer(d, x, y, w, h, ...) 中 y 是 top-left
        centers = _draw_layer(
            d, margin_x, yt, layer_w, h, label, sub, mains, subs, font_main_size=46
        )
        layer_centers.append((yt, h, centers))

    # 纵向箭头:每层节点 → 上一层最近节点
    main_h_px = 80
    for i in range(1, len(layer_centers)):
        yt_curr, h_curr, centers_curr = layer_centers[i]
        yt_prev, h_prev, centers_prev = layer_centers[i - 1]
        for cx, cy in centers_curr:
            target = min(centers_prev, key=lambda p: abs(p[0] - cx))
            px, py = target
            _draw_arrow(d, px, py - main_h_px / 2 - 5, cx, cy + main_h_px / 2 + 5, lw=5)

    # 横切栏(底部)
    cut_y = 1340
    cut_h = 150
    cut_x0 = margin_x
    cut_x1 = margin_x + layer_w
    d.rectangle(
        [(cut_x0, cut_y), (cut_x1, cut_y + cut_h)],
        fill=CROSSCUT_BG,
        outline=CROSSCUT_BORDER,
        width=3,
    )
    f_cut_label = _font(46, True)
    d.text(
        (cut_x0 + 18, cut_y + 22), "Cross-cutting Observability", fill=TEXT_DARK, font=f_cut_label
    )

    obs = ["OpenTelemetry SDK", "Trace Store (Tempo)", "Prometheus metrics", "Grafana dashboards"]
    obs_label_w = 380
    obs_x0 = cut_x0 + obs_label_w
    obs_x1 = cut_x1 - 20
    obs_avail = obs_x1 - obs_x0
    ogap = 20
    ow = (obs_avail - ogap * (len(obs) - 1)) / len(obs)
    oh = 80
    for j, o in enumerate(obs):
        ox0 = obs_x0 + j * (ow + ogap)
        oy0 = cut_y + (cut_h - oh) / 2
        _draw_node(d, ox0, oy0, ow, oh, o, size=36)

    # 贯穿箭头(从横切栏向上示意 OpenTelemetry 跨所有层)
    mid_x = obs_x0 + ow + 100
    _draw_arrow(d, mid_x, cut_y, mid_x, 1100, lw=5)

    # 底部 caption
    f_caption = _font(30, False)
    d.text(
        (60, 1720),
        "Figure 1: 5-Layer RAG Platform Reference Architecture — 2026",
        fill=TEXT_MUTED,
        font=f_caption,
    )

    # Tech stack summary(右上角小框,把核心关键词冗余一遍,OCR 友好)
    tech_box_x0 = w - 700
    tech_box_y0 = 30
    tech_box_w = 660
    tech_box_h = 90
    d.rectangle(
        [(tech_box_x0, tech_box_y0), (tech_box_x0 + tech_box_w, tech_box_y0 + tech_box_h)],
        fill="#F9FAFB",
        outline="#9CA3AF",
        width=2,
    )
    f_tech_title = _font(28, True)
    d.text((tech_box_x0 + 12, tech_box_y0 + 8), "Tech stack:", fill=TEXT_DARK, font=f_tech_title)
    f_tech_line = _font(26, False)
    tech_line1 = "Ollama llama3.1  LLM  +  Ollama nomic-embed-text  +  Chroma Vector DB"
    tech_line2 = "FastAPI  +  PaddleOCR PP-OCRv6  +  pdfplumber  +  pypdfium2"
    d.text((tech_box_x0 + 12, tech_box_y0 + 38), tech_line1, fill=TEXT_DARK, font=f_tech_line)
    d.text((tech_box_x0 + 12, tech_box_y0 + 62), tech_line2, fill=TEXT_DARK, font=f_tech_line)

    img.save(str(path), "PNG", optimize=True)
    return path


# ── p2 组件责任表(reportlab platypus Table,让 pdfplumber 识别为 table) ──
def page2(c):
    """画 Component Responsibility Matrix(pdfplumber extract_tables 能识别)。"""
    _text(c, 50, PAGE_H - 45, "Component Responsibility Matrix", size=18, bold=True)
    _text(
        c,
        50,
        PAGE_H - 65,
        "Per-layer Component / Responsibility / Tech Stack — v1 reference.",
        size=10,
        color=TEXT_MUTED,
    )

    rows = [
        ["Layer", "Component", "Responsibility", "Tech Stack"],
        ["Client", "Web UI", "User chat + sources panel + diagnostics", "Streamlit 1.x + httpx"],
        [
            "Client",
            "Mobile App",
            "On-the-go Q&A via push notifications",
            "React Native + AsyncStorage",
        ],
        ["Edge", "CDN + TLS", "Static caching, TLS termination", "Cloudflare, Let's Encrypt"],
        ["Edge", "Nginx", "Reverse proxy + edge rate-limit", "Nginx 1.25 + lua-resty"],
        [
            "API Gateway",
            "FastAPI Gateway",
            "Single entry point, OpenAPI spec",
            "FastAPI 0.115 + Pydantic v2",
        ],
        ["API Gateway", "Auth (JWT)", "Issue + verify RS256 tokens", "PyJWT + JWKS rotation"],
        [
            "API Gateway",
            "Rate Limiter",
            "Token bucket per IP + per user",
            "slowapi + Redis backend",
        ],
        [
            "Service / Ingestion",
            "Multi-format Loader",
            "PDF/HTML/DOCX/XLSX + OCR fallback",
            "pdfplumber, BeautifulSoup, paddleocr",
        ],
        [
            "Service / Ingestion",
            "Chunker",
            "Recursive + Markdown-heading split",
            "kbchat chunking 0.4",
        ],
        [
            "Service / Ingestion",
            "Embedder",
            "Vectorize chunks to 768-dim",
            "Ollama nomic-embed-text",
        ],
        [
            "Service / Retrieval",
            "Hybrid Retriever",
            "BM25 + dense + RRF fusion",
            "rank_bm25 + Chroma + RRF k=60",
        ],
        [
            "Service / Retrieval",
            "Reranker",
            "Cross-encoder score refinement",
            "BGE-reranker-base (local)",
        ],
        [
            "Service / Generation",
            "LLM",
            "Grounded generation + citation builder",
            "Ollama llama3.1 8B",
        ],
        ["Storage", "Vector DB", "Persist vectors + cosine search", "Chroma PersistentClient"],
        ["Storage", "Cache", "Hot Q/A cache + rate-limit counters", "Redis 7 (sorted sets)"],
        [
            "Storage",
            "Postgres",
            "Documents, users, ACL, audit log",
            "Postgres 16 + pgvector mirror",
        ],
        ["Storage", "Object Store", "Raw uploads + derived artifacts", "S3 / MinIO"],
        [
            "Cross-cutting",
            "OpenTelemetry",
            "Trace every hop, export OTLP",
            "opentelemetry-python + OTLP",
        ],
        [
            "Cross-cutting",
            "Grafana",
            "Latency / error / cost dashboards",
            "Grafana 11 + Prometheus",
        ],
    ]

    # 用 platypus Table —— 它会生成真正的 PDF table 流,pdfplumber 能识别
    tbl = Table(
        rows,
        colWidths=[3.5 * cm, 5 * cm, 7 * cm, 5.5 * cm],
        repeatRows=1,
    )
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), LAYER_BORDER),
                ("TEXTCOLOR", (0, 0), (-1, 0), white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, LAYER_BG]),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, HexColor("#9CA3AF")),
                ("BOX", (0, 0), (-1, -1), 0.8, LAYER_BORDER),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    w, h = tbl.wrapOn(c, PAGE_W - 100, PAGE_H - 150)
    tbl.drawOn(c, 50, PAGE_H - 90 - h)

    _text(
        c,
        50,
        40,
        "Table 1: Per-component responsibilities and tech stack.",
        size=9,
        color=TEXT_MUTED,
    )


# ── p3 数据流文字描述(reportlab 矢量) ──────────────────────
def page3(c):
    _text(c, 50, PAGE_H - 45, "Data Flow Narrative", size=18, bold=True)
    _text(
        c,
        50,
        PAGE_H - 65,
        "End-to-end pipeline: Ingestion, Query, Generation — with latency budget.",
        size=10,
        color=TEXT_MUTED,
    )

    y = PAGE_H - 95
    sections = [
        (
            "Ingestion Pipeline (offline, batch or watch-folder)",
            [
                "1. Watch folder or S3 bucket receives a new PDF, HTML, DOCX, or XLSX upload.",
                "2. Loader parses each file. PDFs go through pdfplumber for text, tables, and image bboxes.",
                "3. Each image region is rendered to PNG via pypdfium2 (scale=2.5), cropped by bbox, then OCR'd with PaddleOCR PP-OCRv6.",
                "4. Chunking: recursive character splitter with overlap 200, or markdown-heading split for structured docs.",
                "5. Embedding: each chunk is vectorized to 768-dim via Ollama nomic-embed-text, batched 32 at a time.",
                "6. Persistence: vectors land in Chroma PersistentClient; metadata in Postgres; raw uploads in S3.",
                "7. OpenTelemetry span closes; trace_id recorded in audit log for post-hoc RAGAS evaluation.",
            ],
        ),
        (
            "Query Pipeline (online, user-initiated)",
            [
                "1. Web UI sends the user question with JWT to FastAPI Gateway.",
                "2. Auth middleware verifies RS256 token against JWKS; Rate Limiter checks token bucket (Redis).",
                "3. Retrieval runs Hybrid Retriever: BM25 (rank_bm25) and Dense (Chroma) in parallel, fused via RRF k=60.",
                "4. Top-20 candidates fed to Cross-Encoder reranker (BGE-reranker-base local), re-ranked to top-5.",
                "5. Context Packer assembles passages with token budget 4000, de-duplicates near-duplicates.",
                "6. Retrieval result + question forwarded to Generation service.",
            ],
        ),
        (
            "Generation Pipeline (online, streaming)",
            [
                "1. Grounded prompt template includes system instructions, retrieved passages, and citation slots.",
                "2. LLM (Ollama llama3.1 8B) streams tokens; citation builder attaches [n] markers to source passages.",
                "3. Response validator rejects answers lacking citation or containing prompt-injection markers.",
                "4. Final answer + sources returned to Web UI; metrics emitted to OpenTelemetry.",
                "5. Audit row written to Postgres with trace_id for end-to-end traceability.",
            ],
        ),
        (
            "Latency Budget (target p95)",
            [
                "Auth + Rate Limit 5ms | Hybrid retrieval 120ms | Rerank 80ms | Context pack 20ms | "
                "LLM first token 350ms | Total p95 less than 700ms.",
            ],
        ),
    ]

    for title, lines in sections:
        _text(c, 50, y, title, size=12, bold=True)
        y -= 16
        for line in lines:
            _text(c, 60, y, line, size=10)
            y -= 13
        y -= 6

    _text(
        c,
        50,
        40,
        "Section: How data flows through the RAG platform — source for QA over this diagram.",
        size=9,
        color=TEXT_MUTED,
    )


# ── 工具:报告书矢量绘制(给 p2/p3 用) ──────────────────────
def _text(c, x, y, txt, size=11, bold=False, color=TEXT_DARK):
    c.setFillColor(color)
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    c.drawString(x, y, txt)


def _rect(c, x, y, w, h, fill=NODE_BG, stroke=NODE_BORDER, lw=1.2):
    # fill 是颜色字符串或 HexColor;reportlab 都接受
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.setLineWidth(lw)
    c.rect(x, y, w, h, fill=1, stroke=1)


# ── 入口 ──────────────────────────────────────────────────
def main():
    # 1) PIL 画 p1 PNG
    draw_p1_png(TMP_P1)
    print(f"✅ p1 PNG: {TMP_P1} ({TMP_P1.stat().st_size:,} B)")

    # 2) reportlab 拼 3 页 PDF
    c = rl_canvas.Canvas(str(OUTPUT), pagesize=landscape(A4))

    # p1: 嵌入 p1 PNG
    c.drawImage(
        str(TMP_P1), 0, 0, width=PAGE_W, height=PAGE_H, preserveAspectRatio=True, anchor="c"
    )
    c.showPage()

    # p2: 矢量
    page2(c)
    c.showPage()

    # p3: 矢量
    page3(c)
    c.showPage()

    c.save()
    print(f"✅ wrote {OUTPUT} ({OUTPUT.stat().st_size:,} B)")


if __name__ == "__main__":
    main()
