"""P5 Image Pipeline UI —— Streamlit 三步走验证 OCR 入库完整链路。

跑:
    ./.venv/bin/streamlit run app.py --server.port 8502
"""

from __future__ import annotations

import json
import shutil
import sys
import urllib.request
from pathlib import Path

import streamlit as st

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from src.chroma_store import P5ChromaStore  # noqa: E402
from src.loaders import load  # noqa: E402
from src.models import BlockType  # noqa: E402
from src.ocr import _check_ready, reset_cache  # noqa: E402

st.set_page_config(page_title="P5 Image Pipeline Demo", layout="wide", page_icon="🖼")

# ── 子标题 ───────────────────────────────────────────────────
st.title("🖼️ P5 Multi-Format Loader — Image Pipeline UI")
st.caption(
    "上传文件 → OCR 提取图片内容 → 写 Chroma 向量库 → 自然语言问答 → "
    "看到 top-1 命中图 block。完整链路可视化。"
)

# ── 状态条 ───────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 控制台")
    backend_ready = _check_ready()
    st.metric("OCR Backend", "✓ ready" if backend_ready else "✗ unavailable")

    if st.button("🗑 清空 Chroma + 缓存", type="secondary"):
        db = HERE / "chroma_db"
        if db.exists():
            shutil.rmtree(db)
        reset_cache()
        st.success("已清空")

    st.divider()
    st.markdown("**📂 当前 fixtures**")
    for f in (
        sorted((HERE / "fixtures").glob("*.png"))
        + sorted((HERE / "fixtures").glob("*.pdf"))
        + sorted((HERE / "fixtures").glob("*.html"))
    ):
        st.caption(f"  📄 {f.name} ({f.stat().st_size:,} B)")


# ── Ollama embedding（项目默认）────────────────────────────
def ollama_embed(texts: list[str]) -> list[list[float]]:
    out = []
    for t in texts:
        body = json.dumps({"model": "nomic-embed-text", "prompt": t}).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                out.append(json.loads(r.read())["embedding"])
        except Exception as e:  # noqa: BLE001
            st.error(f"Ollama 调用失败: {e}")
            out.append([0.0] * 768)
    return out


@st.cache_resource
def get_store():
    return P5ChromaStore(HERE / "chroma_db", embed_fn=ollama_embed)


# ── Tab 1：上传 + 提取 ──────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["1️⃣ 上传 → OCR 提取", "2️⃣ 写 Chroma + 检索", "3️⃣ 问答演示"])

with tab1:
    st.subheader("上传文件 / 用 fixtures")
    col_a, col_b = st.columns(2)

    with col_a:
        src = st.radio(
            "数据源",
            [
                "fixtures/architecture.pdf",
                "fixtures/sample.pdf",
                "fixtures/sample.html",
                "上传本地文件",
            ],
            key="src",
        )
    with col_b:
        if src == "上传本地文件":
            up = st.file_uploader(
                "拖入 PDF / HTML / PNG",
                type=["pdf", "html", "htm", "png", "jpg", "jpeg"],
                key="up",
            )
        else:
            up = None

    if st.button("🔍 提取 Block（含 OCR）", type="primary"):
        if src == "上传本地文件" and up:
            tmp = HERE / f"_upload_{up.name}"
            tmp.write_bytes(up.read())
            target = tmp
        else:
            target = HERE / src

        reset_cache()
        with st.spinner(f"加载 {target.name} ..."):
            try:
                doc = load(target)
            except Exception as e:  # noqa: BLE001
                st.error(f"加载失败: {e}")
                doc = None

        if doc:
            st.session_state["last_doc"] = doc
            st.success(
                f"✅ 抽出 {len(doc.blocks)} 个 Block（"
                f"{ {b.block_type.value for b in doc.blocks} }"
                f"）"
            )

            # 按 block_type 分组显示
            blocks = doc.blocks
            counts: dict[str, int] = {}
            for b in blocks:
                counts[b.block_type.value] = counts.get(b.block_type.value, 0) + 1
            c1, c2, c3, c4 = st.columns(4)
            for col, k in zip(
                (c1, c2, c3, c4), ("text", "heading", "table", "image"), strict=False
            ):
                col.metric(k, counts.get(k, 0))

            # Image blocks 单独展开（OCR 是重点）
            images = [b for b in blocks if b.block_type == BlockType.IMAGE]
            if images:
                st.markdown("### 🖼 Image Blocks（OCR 抽出真内容）")
                for i, img in enumerate(images, 1):
                    with st.expander(
                        f"#{i} [{img.source_format.value} p{img.page_or_sheet}] "
                        f"OCR 文本 {len(img.ocr_text)} 字符",
                        expanded=True,
                    ):
                        st.code(img.text, language="markdown")
                        if img.ocr_text:
                            st.success(f"✅ OCR 命中: {len(img.ocr_text)} 字符真文字")
                        else:
                            st.warning("⚠ OCR 未识别出文字（降级为空）")

            # 其他 block 类型折叠
            with st.expander("📋 全部 Block 详情"):
                for _i, b in enumerate(blocks, 1):
                    st.caption(
                        f"[{b.block_type.value:7s}] {b.source_format.value} p{b.page_or_sheet}"
                    )
                    st.code(
                        b.text[:300] + ("..." if len(b.text) > 300 else ""), language="markdown"
                    )


# ── Tab 2：写库 + 检索 ──────────────────────────────────────
with tab2:
    st.subheader("把抽取结果写进 Chroma 向量库")

    if "last_doc" not in st.session_state:
        st.info("先去 Tab 1 加载文件")
        st.stop()

    doc = st.session_state["last_doc"]
    store = get_store()

    col1, col2 = st.columns([1, 2])
    with col1:
        if st.button("💾 写入 Chroma"):
            with st.spinner("embedding ..."):
                n_added = store.add_document(doc)
            st.session_state["chroma_count"] = store.count()
            st.success(f"新增 {n_added} 条，总计 {store.count()} 条")

    with col2:
        st.metric("Chroma 当前记录数", store.count())

    st.divider()
    st.markdown("### 🔍 语义检索（Ollama embedding）")

    q = st.text_input("问一句", placeholder="例如：APAC revenue 240M / laptop $1499")
    if st.button("检索", key="q_btn") and q:
        with st.spinner("向量检索中 ..."):
            results = store.query(q, k=5)
        for r in results:
            mt = r["metadata"]
            col_t, col_m, col_d = st.columns([3, 2, 1])
            with col_t:
                kind = mt.get("block_type", "?")
                snippet = r["text"][:200].replace(chr(10), " ")
                st.markdown(f"**[{kind}]** {snippet}")
            with col_m:
                st.caption(f"{mt.get('source_format', '?')} | {mt.get('source_path', '?')[-30:]}")
            with col_d:
                st.caption(f"dist={r['distance']:.3f}")
            # 如果是 image block、且有路径，显示图
            if mt.get("block_type") == "image" and mt.get("source_path"):
                sp = Path(mt["source_path"])
                if sp.suffix.lower() == ".pdf":
                    # PDF 图：没存原图二进制，给出页码提示
                    st.caption(f"📄 PDF p{mt.get('page_or_sheet')} · OCR 文本已显示在上方")
                else:
                    # HTML 图有 image_path metadata
                    img_meta = mt.get("image_path")
                    if img_meta and Path(img_meta).exists():
                        st.image(str(img_meta), caption=f"原图 {Path(img_meta).name}", width=400)
            st.divider()


# ── Tab 3：问答演示 ─────────────────────────────────────────
with tab3:
    st.subheader("🤖 端到端问答")
    store = get_store()

    st.markdown("**演示问题（fixture 内已埋关键词）：**")
    demos = [
        "APAC region revenue chart 240M",  # → sample.pdf p1 图 OCR
        "Regional growth rate APAC 32%",  # → sample.pdf p2 图 OCR
        "laptop price 1499 catalog",  # → HTML 图 OCR + 表格
        "headcount engineering",  # → XLSX 表格
        # architecture.pdf (新) 相关
        "system architecture diagram components",  # → architecture.pdf p1 image
        "which database stores embeddings",  # → architecture.pdf p1 image + p2 table
        "ingestion pipeline steps PDF",  # → architecture.pdf p3 text
        "rate limiter JWT authentication",  # → architecture.pdf p1 image
        "what reranker does retrieval use",  # → architecture.pdf p1 image + p3 text
        "where does OpenTelemetry send traces",  # → architecture.pdf p1 image
    ]
    for d in demos:
        st.caption(f"  • {d}")

    q = st.text_input("你的问题", key="qa_q")
    top_k = st.slider("top_k", 1, 10, 3)

    if st.button("🚀 问", key="qa_btn") and q:
        with st.spinner("向量检索 ..."):
            results = store.query(q, k=top_k)
        if not results:
            st.warning("没命中。试试先在 Tab 2 写库。")
        else:
            st.markdown(f"### Top {len(results)} 命中")
            for rank, r in enumerate(results, 1):
                mt = r["metadata"]
                with st.container():
                    cols = st.columns([1, 6])
                    with cols[0]:
                        st.markdown(f"### #{rank}")
                        st.metric("dist", f"{r['distance']:.3f}")
                    with cols[1]:
                        kind = mt.get("block_type", "?")
                        st.markdown(
                            f"**[{kind}]** *{mt.get('source_format', '?')}* · p{mt.get('page_or_sheet', '?')}"
                        )
                        st.code(
                            r["text"][:400] + ("..." if len(r["text"]) > 400 else ""),
                            language="markdown",
                        )
                        # 图块尝试显示原图
                        if mt.get("block_type") == "image":
                            img_path = mt.get("image_path") or mt.get("source_path")
                            if img_path and Path(img_path).suffix.lower() in (
                                ".png",
                                ".jpg",
                                ".jpeg",
                            ):
                                if Path(img_path).exists():
                                    st.image(str(img_path), width=400)
                st.divider()

st.caption(f"📍 Chroma 持久化目录: `{HERE / 'chroma_db'}`")
