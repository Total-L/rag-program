"""P6 — 高级分块与元数据增强（"怎么优化数据"的第二步）。

审计发现：`p5/src/chunkers/` 是**空目录**（只有一个空的 `__init__.py`），
承诺的 layout-aware / table-aware / parent-child / contextual chunking 全都没实现。
而分块策略对召回的影响通常比换嵌入模型更大。

本模块四种策略，按"从便宜到贵"排列：

| 策略                | 解决什么问题                          | 代价        |
|---------------------|---------------------------------------|-------------|
| `layout_chunks`     | 按标题层级切，不切碎语义单元          | 免费        |
| `table_aware_chunks`| 表格整体保留 + 表头随行（否则行失去语义）| 免费        |
| `parent_child_chunks`| 小块精准召回 + 大块提供上下文          | 存储翻倍    |
| `contextual_chunks` | 给每块加一句"它在讲什么"的上下文前缀   | 每块一次 LLM |

`contextual_chunks` 是 Anthropic contextual retrieval 的思路：
孤立的 chunk 常常丢掉主语（"该比例不得超过 15%" —— 什么比例？），
在嵌入前补一句上下文，召回能明显改善。这里默认用**免 LLM 的启发式**
（标题路径 + 文档标题），也支持传 `llm_fn` 用真模型生成。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .models import Chunk

# ── 标题识别 ──────────────────────────────────────────
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
# 中文文档常见的编号标题："一、总则" / "1.2 适用范围" / "第三条"
_CN_HEADING = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百]+[条章节]|[一二三四五六七八九十]+、|\d+(?:\.\d+)*\s)\s*\S+"
)
# re.MULTILINE 是必须的：既要能 .match(单行) 判断表格行，
# 也要能 .search(整段多行文本) 判断"这段里有没有表格"。
_MD_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)


@dataclass
class Section:
    """一个带标题路径的文档片段。"""

    heading_path: str
    text: str
    level: int = 0
    is_table: bool = False


def split_sections(text: str) -> list[Section]:
    """按标题层级切分，维护完整标题路径（layout-aware 的核心）。

    为什么要标题路径而不只是标题：
    "限额" 这个标题单独看毫无意义，"报销政策 > 差旅 > 限额" 才可检索。
    P1 用的 kbchat MarkdownHeadingSplitter 有类似能力，这里额外支持中文编号标题。
    """
    lines = text.split("\n")
    stack: list[tuple[int, str]] = []  # [(level, title)]
    sections: list[Section] = []
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            path = " > ".join(t for _, t in stack)
            sections.append(
                Section(heading_path=path, text=body, level=stack[-1][0] if stack else 0)
            )
        buf.clear()

    for line in lines:
        m = _MD_HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2).strip()))
            continue
        if _CN_HEADING.match(line) and len(line.strip()) <= 40:
            flush()
            # 中文编号标题统一当作次级标题（拿不到可靠层级就别硬猜）
            while stack and stack[-1][0] >= 9:
                stack.pop()
            stack.append((9, line.strip()))
            continue
        buf.append(line)
    flush()
    return sections


def layout_chunks(text: str, *, max_chars: int = 600, overlap: int = 80) -> list[Section]:
    """标题感知切分 + 超长段落的兜底再切。

    `overlap` 只在**不得不硬切**时使用：
    按标题切出来的块天然语义完整，不需要重叠；只有一段正文本身超长才要重叠兜底。
    """
    out: list[Section] = []
    for sec in split_sections(text):
        if len(sec.text) <= max_chars:
            out.append(sec)
            continue
        for piece in _split_long(sec.text, max_chars=max_chars, overlap=overlap):
            out.append(Section(heading_path=sec.heading_path, text=piece, level=sec.level))
    return out


def _split_long(text: str, *, max_chars: int, overlap: int) -> list[str]:
    """先按段落攒，攒不下再按句子硬切（尽量不在句中断开）。"""
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) + 2 <= max_chars:
            cur = f"{cur}\n\n{p}" if cur else p
            continue
        if cur:
            chunks.append(cur)
        if len(p) <= max_chars:
            cur = p
            continue
        # 单段就超长 → 按句子边界切，带 overlap
        sents = re.split(r"(?<=[。！？.!?])\s*", p)
        cur = ""
        for s in sents:
            if len(cur) + len(s) <= max_chars:
                cur += s
            else:
                if cur:
                    chunks.append(cur)
                    cur = (cur[-overlap:] if overlap else "") + s
                else:
                    chunks.append(s[:max_chars])
                    cur = s[max_chars:]
    if cur:
        chunks.append(cur)
    return [c.strip() for c in chunks if c.strip()]


def table_aware_chunks(text: str, *, max_rows: int = 12) -> list[Section]:
    """表格单独成块，并让**表头随每个分片重复**。

    这是表格召回的关键：一张 50 行的表硬切成 5 块，后 4 块没有表头，
    "限额 800" 这一行就完全失去语义。企业文档里表格密度极高（报销标准、
    权限矩阵、SLA），这一条能直接决定财务/HR 类问题答不答得对。
    """
    out: list[Section] = []
    buf: list[str] = []
    table: list[str] = []
    heading = ""

    def flush_text() -> None:
        body = "\n".join(buf).strip()
        if body:
            out.append(Section(heading_path=heading, text=body))
        buf.clear()

    def flush_table() -> None:
        if not table:
            return
        header = table[:2] if len(table) >= 2 else table[:1]  # | h | + |---|
        body_rows = table[2:] if len(table) >= 2 else []
        if not body_rows:
            out.append(Section(heading_path=heading, text="\n".join(table), is_table=True))
        else:
            for i in range(0, len(body_rows), max_rows):
                part = header + body_rows[i : i + max_rows]
                out.append(Section(heading_path=heading, text="\n".join(part), is_table=True))
        table.clear()

    for line in text.split("\n"):
        m = _MD_HEADING.match(line)
        if m:
            flush_table()
            flush_text()
            heading = m.group(2).strip()
            continue
        if _MD_TABLE_ROW.match(line):
            flush_text()
            table.append(line.rstrip())
            continue
        flush_table()
        buf.append(line)
    flush_table()
    flush_text()
    return out


def parent_child_chunks(
    text: str,
    source_id: str,
    *,
    child_chars: int = 300,
    parent_chars: int = 1200,
) -> tuple[list[Chunk], list[Chunk]]:
    """父子分块：子块用于**精准召回**，父块用于**喂给 LLM 的上下文**。

    检索时用子块（短、向量聚焦、命中率高），
    生成时把命中子块的父块整块塞进 prompt（上下文完整、不断句）。
    代价是存储翻倍，收益是"召回准 + 上下文全"两头都要到。
    """
    parents: list[Chunk] = []
    children: list[Chunk] = []
    for p_ord, psec in enumerate(layout_chunks(text, max_chars=parent_chars, overlap=0)):
        pid = Chunk.make_id(source_id, psec.heading_path, psec.text)
        parents.append(
            Chunk(
                chunk_id=pid,
                source_id=source_id,
                text=psec.text,
                ordinal=p_ord,
                heading_path=psec.heading_path,
                chunk_type="parent",
            )
        )
        for c_ord, piece in enumerate(_split_long(psec.text, max_chars=child_chars, overlap=40)):
            children.append(
                Chunk(
                    chunk_id=Chunk.make_id(source_id, psec.heading_path, piece),
                    source_id=source_id,
                    text=piece,
                    ordinal=c_ord,
                    heading_path=psec.heading_path,
                    parent_id=pid,
                    chunk_type="child",
                )
            )
    return parents, children


def contextual_prefix(
    *,
    doc_title: str,
    heading_path: str,
    text: str,
    llm_fn: Callable[[str], str] | None = None,
) -> str:
    """给 chunk 生成上下文前缀（Anthropic contextual retrieval 思路）。

    默认启发式（免费、确定性）：`文档标题 > 标题路径`。
    传 `llm_fn` 则用模型生成一句更贴切的上下文 —— 效果更好但每块一次调用，
    100 万 chunk 就是 100 万次调用，成本必须提前算清楚。
    """
    if llm_fn is not None:
        prompt = (
            f"文档《{doc_title}》的章节「{heading_path}」中有如下片段：\n{text[:500]}\n\n"
            "用一句话（不超过 30 字）说明这个片段在讲什么，用于改善检索。只输出这句话。"
        )
        try:
            got = llm_fn(prompt).strip().split("\n")[0]
            if got:
                return got[:100]
        except Exception:
            pass  # LLM 失败不能拖垮摄取，退回启发式
    parts = [p for p in (doc_title, heading_path) if p]
    return " > ".join(parts)


def enrich_metadata(text: str, *, heading_path: str = "") -> dict[str, object]:
    """轻量元数据增强：关键词 + 是否含表格 + 长度特征。

    刻意只做**确定性、零成本**的增强。摘要/生成问题/实体抽取需要 LLM，
    放在 `contextual_prefix(llm_fn=...)` 里按需开启，不在这里偷偷烧钱。
    """
    return {
        "n_chars": len(text),
        "has_table": bool(_MD_TABLE_ROW.search(text)),
        "has_number": bool(re.search(r"\d", text)),
        "heading_depth": heading_path.count(">") + 1 if heading_path else 0,
        "keywords": "|".join(_keywords(text)),
    }


_STOP = set(
    "的了和是在与及或对为以由于该等要不有这那我们你们他们及其如果因为所以a an the of to and or in on for is are be".split()
)


def _keywords(text: str, top: int = 5) -> list[str]:
    """极简关键词：中文双字词 + 英文单词的词频 top-N。"""
    counts: dict[str, int] = {}
    for w in re.findall(r"[a-zA-Z]{3,}", text.lower()):
        if w not in _STOP:
            counts[w] = counts.get(w, 0) + 1
    zh = re.sub(r"[^一-鿿]", "", text)
    for i in range(len(zh) - 1):
        bigram = zh[i : i + 2]
        if bigram not in _STOP:
            counts[bigram] = counts.get(bigram, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:top]]


def to_chunks(
    sections: Iterable[Section],
    source_id: str,
    *,
    doc_title: str = "",
    add_context: bool = True,
    llm_fn: Callable[[str], str] | None = None,
) -> list[Chunk]:
    """Section → Chunk，顺带做上下文前缀与元数据增强。

    注意：上下文前缀参与 `chunk_id` 的计算（因为它会进嵌入文本），
    所以改前缀策略会让全库 chunk_id 变化 → 需要一次全量重建。这点必须知情。
    """
    out: list[Chunk] = []
    for i, sec in enumerate(sections):
        body = sec.text
        if add_context:
            prefix = contextual_prefix(
                doc_title=doc_title,
                heading_path=sec.heading_path,
                text=body,
                llm_fn=llm_fn,
            )
            if prefix:
                body = f"[{prefix}]\n{sec.text}"
        md = enrich_metadata(sec.text, heading_path=sec.heading_path)
        out.append(
            Chunk(
                chunk_id=Chunk.make_id(source_id, sec.heading_path, body),
                source_id=source_id,
                text=body,
                ordinal=i,
                heading_path=sec.heading_path,
                chunk_type="table" if sec.is_table else "text",
                metadata=md,
            )
        )
    return out


__all__ = [
    "Section",
    "split_sections",
    "layout_chunks",
    "table_aware_chunks",
    "parent_child_chunks",
    "contextual_prefix",
    "enrich_metadata",
    "to_chunks",
]
