"""P6 分块单测 —— 填补 `p5/src/chunkers/` 空目录的那些能力。

覆盖意图：证明分块策略解决了具体的召回问题，而不只是"切出了 N 块"。
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src.chunkers import (  # noqa: E402
    contextual_prefix,
    enrich_metadata,
    layout_chunks,
    parent_child_chunks,
    split_sections,
    table_aware_chunks,
    to_chunks,
)
from src.models import Chunk  # noqa: E402

DOC = """# 报销政策

## 差旅

### 限额
该比例不得超过 15%。市内交通每天上限 200 元。

## 标准表

| 类别 | 限额 | 备注 |
|---|---|---|
| 市内交通 | 200/天 | 凭票 |
| 住宿 | 800/晚 | 一线城市 |
| 餐补 | 100/天 | 无需票 |
| 长途 | 实报 | 需审批 |
"""


# ── layout-aware：标题路径解决"无主语"问题 ──────────────
def test_heading_path_gives_orphan_text_a_subject():
    """「该比例不得超过 15%」孤立看毫无意义，必须带上完整标题路径。"""
    secs = split_sections(DOC)
    target = [s for s in secs if "该比例" in s.text]
    assert target, "应切出含该句的片段"
    assert target[0].heading_path == "报销政策 > 差旅 > 限额", target[0].heading_path


def test_split_sections_handles_chinese_numbered_headings():
    doc = "# 制度\n\n一、总则\n本制度适用于全体员工。\n\n二、罚则\n违规者按规定处理。"
    secs = split_sections(doc)
    paths = [s.heading_path for s in secs]
    assert any("总则" in p for p in paths), paths
    assert any("罚则" in p for p in paths), paths


def test_layout_chunks_respects_max_chars():
    long_doc = "# 标题\n\n" + ("这是一段很长的正文内容。" * 200)
    chunks = layout_chunks(long_doc, max_chars=300, overlap=40)
    assert len(chunks) > 1, "超长段落必须被切开"
    # 允许少量超出（不在句中硬断），但不能失控
    assert all(len(c.text) <= 600 for c in chunks), [len(c.text) for c in chunks]
    assert all(c.heading_path == "标题" for c in chunks), "切开后标题路径要保留"


def test_layout_chunks_keeps_short_section_intact():
    chunks = layout_chunks(DOC, max_chars=2000)
    assert all("\n\n\n" not in c.text for c in chunks)
    assert any("该比例" in c.text for c in chunks)


# ── table-aware：表头随行 ──────────────────────────────
def test_table_split_repeats_header_in_every_part():
    """一张表切成多片时，后面的片没有表头 → 「800/晚」就失去语义。"""
    parts = [s for s in table_aware_chunks(DOC, max_rows=2) if s.is_table]
    assert len(parts) >= 2, f"4 行表 max_rows=2 应切 2 片, got {len(parts)}"
    for p in parts:
        assert "类别" in p.text and "限额" in p.text, f"分片缺表头: {p.text!r}"


def test_table_rows_are_not_lost():
    parts = [s for s in table_aware_chunks(DOC, max_rows=2) if s.is_table]
    joined = "\n".join(p.text for p in parts)
    for row in ["市内交通", "住宿", "餐补", "长途"]:
        assert row in joined, f"丢行: {row}"


def test_table_and_text_are_separated():
    secs = table_aware_chunks(DOC, max_rows=10)
    assert any(s.is_table for s in secs), "应识别出表格块"
    assert any(not s.is_table and s.text.strip() for s in secs), "应保留正文块"


# ── parent-child ────────────────────────────────────
def test_parent_child_pointers_are_valid():
    parents, children = parent_child_chunks(
        DOC * 3, "fs:policy.md", child_chars=150, parent_chars=600
    )
    assert parents and children
    pids = {p.chunk_id for p in parents}
    assert all(c.parent_id in pids for c in children), "子块必须指向存在的父块"
    assert all(c.chunk_type == "child" for c in children)
    assert all(p.chunk_type == "parent" for p in parents)


def test_children_are_smaller_than_parents():
    parents, children = parent_child_chunks(
        DOC * 3, "fs:policy.md", child_chars=150, parent_chars=600
    )
    assert max(len(c.text) for c in children) <= max(len(p.text) for p in parents)


# ── contextual retrieval ────────────────────────────
def test_contextual_prefix_heuristic_is_free_and_deterministic():
    p1 = contextual_prefix(doc_title="报销政策 2026", heading_path="差旅 > 限额", text="x")
    p2 = contextual_prefix(doc_title="报销政策 2026", heading_path="差旅 > 限额", text="x")
    assert p1 == p2 == "报销政策 2026 > 差旅 > 限额"


def test_contextual_prefix_uses_llm_when_given():
    got = contextual_prefix(
        doc_title="T", heading_path="H", text="body", llm_fn=lambda p: "这段讲差旅报销限额"
    )
    assert got == "这段讲差旅报销限额"


def test_contextual_prefix_falls_back_when_llm_fails():
    """LLM 挂了不能拖垮整条摄取流水线。"""

    def boom(_):
        raise RuntimeError("LLM down")

    got = contextual_prefix(doc_title="T", heading_path="H", text="b", llm_fn=boom)
    assert got == "T > H", "LLM 失败应静默退回启发式"


# ── to_chunks + 元数据增强 ────────────────────────────
def test_to_chunks_adds_prefix_and_metadata():
    chunks = to_chunks(layout_chunks(DOC), "fs:policy.md", doc_title="报销政策 2026")
    assert chunks
    assert chunks[0].text.startswith("["), "应带上下文前缀"
    md = chunks[0].metadata
    assert md["n_chars"] > 0
    assert "keywords" in md
    assert isinstance(md["has_table"], bool)


def test_chunk_ids_are_unique_and_stable():
    a = to_chunks(layout_chunks(DOC), "fs:policy.md", doc_title="T")
    b = to_chunks(layout_chunks(DOC), "fs:policy.md", doc_title="T")
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b], "同输入必须同 id（增量的前提）"
    assert len({c.chunk_id for c in a}) == len(a), "同文档内 chunk_id 不得重复"


def test_chunk_id_changes_when_content_changes():
    base = Chunk.make_id("s", "H", "原始内容")
    edited = Chunk.make_id("s", "H", "修改后内容")
    assert base != edited, "内容变必须换 id，这样 reconcile 才能删掉旧的"


def test_enrich_metadata_flags_table_and_numbers():
    md = enrich_metadata("| a | b |\n|---|---|\n| 1 | 2 |", heading_path="表 > 子表")
    assert md["has_table"] is True
    assert md["has_number"] is True
    assert md["heading_depth"] == 2


def test_to_chunks_empty_input():
    assert to_chunks([], "fs:x.md") == []
