"""P1 Streamlit App：三面板 Chat / Sources / Diagnostics。

跑：
    streamlit run projects/p1-enterprise-kb/src/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
from acl import User
from pipeline import P1Pipeline

st.set_page_config(page_title="Enterprise KB Assistant", layout="wide", page_icon="📚")

ROLE_OPTIONS = ["intern", "employee", "manager", "executive", "admin"]

# ── 侧栏：用户角色 + 配置 ──────────────────────────────
with st.sidebar:
    st.title("⚙️ Settings")
    role = st.selectbox("User role", ROLE_OPTIONS, index=2)
    st.caption("Role controls ACL: public/internal/restricted")


# ── 初始化 pipeline（用 st.cache_resource）────────────
@st.cache_resource
def get_pipeline(role: str) -> P1Pipeline:
    p = P1Pipeline(user=User(user_id="u1", role=role))
    p.index_corpus()
    return p


pipeline = get_pipeline(role)
user = pipeline.user

# ── 三栏布局 ─────────────────────────────────────────
col_chat, col_src, col_diag = st.columns([3, 2, 2])

# 1. Chat 面板
with col_chat:
    st.title("📚 Enterprise KB Assistant")
    st.caption(f"Indexed: {len(pipeline.index)} chunks | Role: **{user.role}**")
    if "history" not in st.session_state:
        st.session_state.history = []
    for h in st.session_state.history:
        with st.chat_message(h["role"]):
            st.write(h["content"])
    if q := st.chat_input("Ask anything about the company..."):
        st.session_state.history.append({"role": "user", "content": q})
        with st.chat_message("user"):
            st.write(q)
        r = pipeline.ask(q)
        st.session_state.history.append({"role": "assistant", "content": r.answer})
        with st.chat_message("assistant"):
            st.write(r.answer)
            st.caption(f" 引用: {r.citations}  拒答: {r.abstained}  {r.latency_ms:.0f}ms")

# 2. Sources 面板
with col_src:
    st.subheader("🔗 Sources")
    if st.session_state.history:
        last_q = next(
            (h["content"] for h in reversed(st.session_state.history) if h["role"] == "user"), ""
        )
        if last_q:
            r = pipeline.ask(last_q)
            for c in r.citations:
                st.markdown(f"**[{c}]** — chunk_id={c}")
    else:
        st.info("Ask a question to see sources.")

# 3. Diagnostics 面板
with col_diag:
    st.subheader("📊 Diagnostics")
    st.metric("Indexed chunks", len(pipeline.index))
    st.metric("User role", user.role)
    st.metric(
        "Visible sensitivity", "≤ internal" if user.role in ("employee", "manager") else "all"
    )
    st.caption("Trace log: data/traces/trace.jsonl")
