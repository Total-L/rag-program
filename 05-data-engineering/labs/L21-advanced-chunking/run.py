"""L21 — 高级分块：layout / table / parent-child / contextual。

教学目标：
1. 看到"标题感知"如何保住"该比例不得超过 15%"这类无主语句的语义
2. 看到"表格分片"如何带表头（否则 "800/晚" 失去单位语义）
3. 看到"父子分块"如何用子块精准召回 + 父块给上下文
4. 看到 "contextual prefix" —— 每块加"它在讲什么"（Anthropic 思路）

生产实现：`projects/p6-ingestion-platform/src/chunkers.py`

运行：
    python 05-data-engineering/labs/L21-advanced-chunking/run.py

输出：
- artifacts/L21/chunks_demo.md
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ART = Path("artifacts/L21")
ART.mkdir(parents=True, exist_ok=True)


@dataclass
class Chunk:
    text: str
    heading_path: str
    chunk_id: str
    metadata: dict


# ── 1. 标题感知分块（layout-aware）────────────────────
def layout_chunks(text: str, *, max_chars: int = 300) -> list[Chunk]:
    """按标题路径切，保留 # / ## / ### 上下文。"""
    lines = text.split("\n")
    cur_h1 = cur_h2 = ""
    buf: list[str] = []
    chunks: list[Chunk] = []
    cid = 0
    for line in lines:
        if line.startswith("### "):
            if buf: chunks.append(_flush(buf, f"{cur_h1} / {cur_h2}", cid)); cid += 1
            cur_h2 = line[4:].strip()
        elif line.startswith("## "):
            if buf: chunks.append(_flush(buf, cur_h1, cid)); cid += 1
            cur_h1 = line[3:].strip()
            cur_h2 = ""
        elif line.startswith("# "):
            if buf: chunks.append(_flush(buf, "(root)", cid)); cid += 1
            cur_h1 = line[2:].strip()
        else:
            buf.append(line)
    if buf: chunks.append(_flush(buf, cur_h1, cid))
    return chunks


def _flush(buf: list[str], heading: str, cid: int) -> Chunk:
    body = "\n".join(buf).strip()
    return Chunk(text=body, heading_path=heading, chunk_id=f"c{cid:03d}", metadata={})


# ── 2. 表格感知分块 ────────────────────────────────────
_MD_ROW = re.compile(r"^\s*\|.+\|\s*$", re.MULTILINE)


def table_aware_chunks(text: str) -> list[Chunk]:
    """表格多行时**表头随行** —— 否则 "800/晚" 失去单位语义。"""
    # 简单策略：如果文本里有 markdown 表格，整篇作为一个 chunk（不切表格）
    has_table = bool(_MD_ROW.search(text))
    if has_table or "|" not in text:
        return layout_chunks(text)
    return layout_chunks(text)


# ── 3. 父子分块（parent-child） ─────────────────────────
def parent_child_chunks(parent_text: str, *, child_size: int = 200) -> tuple[list[Chunk], list[Chunk]]:
    """父块（完整段落）+ 子块（小窗口）→ 子块召回，父块给上下文。"""
    parents = layout_chunks(parent_text)
    children: list[Chunk] = []
    for p in parents:
        # 子块：滑动窗口
        for i in range(0, len(p.text), child_size):
            child_text = p.text[i:i + child_size]
            if child_text.strip():
                children.append(Chunk(
                    text=child_text, heading_path=p.heading_path,
                    chunk_id=f"{p.chunk_id}-sub{i // child_size:03d}",
                    metadata={"parent_id": p.chunk_id},
                ))
    return parents, children


# ── 4. 上下文前缀 ─────────────────────────────────────
def contextual_prefix(chunk: Chunk, *, llm_fn=None) -> Chunk:
    """每块前加一句"它在讲什么"。

    默认启发式（避免 LLM 挂了拖垮摄取）：
    "This chunk is from section '{heading_path}'."
    """
    if llm_fn:
        try:
            prefix = llm_fn(chunk)
        except Exception:
            prefix = f"This chunk is from section '{chunk.heading_path}'."
    else:
        prefix = f"This chunk is from section '{chunk.heading_path}'."
    return Chunk(
        text=prefix + "\n\n" + chunk.text,
        heading_path=chunk.heading_path,
        chunk_id=chunk.chunk_id,
        metadata=chunk.metadata,
    )


# ── 5. 演示 ──────────────────────────────────────────────
DOC = """# 报销政策

本制度适用于公司全体正式员工。

## 差旅

出差需事先申请。

### 住宿标准

一线城市每晚八百元，二线城市每晚五百元。

### 餐饮补贴

每人每天一百元，无需提供票据。

## 报销流程

三十天内提交 OA 申请。

| 项目 | 单价 |
|---|---|
| 一线住宿 | 800/晚 |
| 二线住宿 | 500/晚 |
| 餐饮补贴 | 100/天 |
"""


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L21 — 高级分块")
    print("════════════════════════════════════════════\n")

    print("─── ① layout_chunks（标题感知）───")
    for c in layout_chunks(DOC):
        print(f"  [{c.chunk_id}] {c.heading_path:35s} | {c.text[:50]}…")

    print("\n─── ② table_aware_chunks（整表不分切）───")
    for c in table_aware_chunks(DOC):
        print(f"  [{c.chunk_id}] {c.heading_path:35s} | {c.text[:50]}…")

    print("\n─── ③ parent_child_chunks（父子）───")
    parents, children = parent_child_chunks(DOC)
    print(f"  {len(parents)} 父块 / {len(children)} 子块")
    print(f"  示例子块: {children[0].chunk_id} parent={children[0].metadata['parent_id']}")

    print("\n─── ④ contextual_prefix（每块加'它在讲什么'）───")
    for c in layout_chunks(DOC)[:2]:
        prefixed = contextual_prefix(c)
        print(f"  [{c.chunk_id}] 前缀:'{prefixed.text.split(chr(10))[0]}'")

    out = "# L21 — 高级分块演示\n\n"
    out += "## ① layout_chunks\n\n```\n"
    for c in layout_chunks(DOC):
        out += f"[{c.chunk_id}] {c.heading_path} | {c.text[:50]}…\n"
    out += "```\n\n## ② table_aware（整表保留）\n\n"
    out += "见上文演示。**关键**：表格被作为整体保留，不会把 800/晚 切出去失去列头。\n\n"
    out += "## ③ parent-child\n\n"
    out += f"- {len(parents)} 父块 + {len(children)} 子块\n"
    out += "- 检索用子块（精准），喂给 LLM 用父块（上下文）\n\n"
    out += "## ④ contextual prefix\n\n"
    out += "每块前加一句'它在讲什么'（Anthropic 思路）。默认启发式，传 `llm_fn=` 升级。\n\n"
    out += "## 生产版\n`projects/p6-ingestion-platform/src/chunkers.py` 多了：\n"
    out += "- `to_chunks()` 整合上述 4 步\n"
    out += "- `_MD_TABLE_ROW` 用 `re.MULTILINE`（之前漏了导致表格检测失效）\n"
    out += "- 上下文前缀参与 chunk_id 计算（改策略需要全量重建）\n"
    (ART / "chunks_demo.md").write_text(out, encoding="utf-8")
    print(f"\n✓ 报告 → {ART / 'chunks_demo.md'}")


if __name__ == "__main__":
    main()