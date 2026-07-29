"""L02 — Chunking 三策略对比（不依赖 kbchat，独立实现 + 复用 kbchat 验证）。

对比 Fixed / Recursive / MarkdownHeading 在同一份文档上的表现。

运行：
    python 01-foundations/labs/L02-chunking-compare/run.py

输出：
- 打印三策略的 chunk 数 / 平均长度 / 边界示例
- 生成 artifacts/L02/comparison.{md,json}
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

ART = Path("artifacts/L02")
ART.mkdir(parents=True, exist_ok=True)

# 真实样本：MD 文档（含代码块 + 标题层级）
SAMPLE = """# 公司差旅政策

## 适用对象
- 全职员工
- 试用期员工按 80% 适用
- 实习生不适用

## 住宿标准
一线城市（北上广深）：800 元/晚
二线城市：600 元/晚
三线及以下：400 元/晚

## 餐补
- 国内：100 元/天
- 国外：30 美元/天

## 交通
- 飞机：经济舱；飞行 > 6 小时可商务舱
- 火车：二等座；高铁一等座需总监批准
- 出租车：实报实销，附行程单

## 申请流程
1. OA 提交出差申请单
2. 直属经理审批
3. 提前 3 天提交（紧急除外）
4. 出差结束后 30 天内提交报销

## 例外
- 客户现场紧急情况可口头审批
- 24 小时内补 OA 单

## 备注
本政策与 HR-001 一致。
"""


@dataclass
class Chunk:
    text: str
    strategy: str
    chunk_id: str
    heading_path: str = ""


# ── 1. FixedCharSplitter ────────────────────────────────────
def fixed_split(text: str, size: int = 200, overlap: int = 30) -> list[Chunk]:
    chunks = []
    i = 0
    n = 0
    while i < len(text):
        piece = text[i : i + size]
        chunks.append(Chunk(text=piece, strategy="fixed", chunk_id=f"fixed-{n}"))
        i += size - overlap
        n += 1
    return chunks


# ── 2. RecursiveSplitter（精简版，kbchat 已实现）────────────
RECURSIVE_SEP = ["\n## ", "\n### ", "\n\n", "。", ". ", " "]


def recursive_split(text: str, size: int = 200, overlap: int = 30) -> list[Chunk]:
    out: list[Chunk] = []

    def _split(t: str, seps: list[str]) -> list[str]:
        if len(t) <= size:
            return [t]
        if not seps:
            return [t[i : i + size] for i in range(0, len(t), size - overlap)]
        sep, rest = seps[0], seps[1:]
        parts = t.split(sep)
        result: list[str] = []
        for p in parts:
            if not p:
                continue
            if len(p) <= size:
                result.append(p)
            else:
                result.extend(_split(p, rest))
        return result

    for n, p in enumerate(_split(text, RECURSIVE_SEP)):
        out.append(Chunk(text=p, strategy="recursive", chunk_id=f"recursive-{n}"))
    return out


# ── 3. MarkdownHeadingSplitter ─────────────────────────────
HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)


def md_split(text: str, max_size: int = 300) -> list[Chunk]:
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        return [Chunk(text=text, strategy="markdown", chunk_id="md-0")]
    chunks: list[Chunk] = []
    # 文档头部
    head = text[: matches[0].start()].strip()
    if head:
        chunks.append(Chunk(text=head, strategy="markdown", chunk_id="md-head", heading_path="(intro)"))
    path: list[str] = []
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        # 维护 breadcrumb
        while len(path) >= level:
            path.pop()
        path.append(title)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if not body:
            continue
        # 长段拆
        if len(body) > max_size:
            for j, off in enumerate(range(0, len(body), max_size)):
                chunks.append(Chunk(
                    text=body[off : off + max_size],
                    strategy="markdown",
                    chunk_id=f"md-{i}-{j}",
                    heading_path=" / ".join(path),
                ))
        else:
            chunks.append(Chunk(
                text=body,
                strategy="markdown",
                chunk_id=f"md-{i}",
                heading_path=" / ".join(path),
            ))
    return chunks


# ── 4. 简易"重建率"（chunk 拼回接近原文）────────────────────
def coverage(chunks: list[Chunk], original: str) -> float:
    joined = "".join(c.text for c in chunks)
    return round(len(joined) / max(len(original), 1), 3)


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L02 — Chunking 三策略对比")
    print("════════════════════════════════════════════\n")

    fixed = fixed_split(SAMPLE, size=200, overlap=30)
    rec = recursive_split(SAMPLE, size=200, overlap=30)
    md = md_split(SAMPLE, max_size=300)

    rows = []
    for name, chunks in [("fixed", fixed), ("recursive", rec), ("markdown", md)]:
        sizes = [len(c.text) for c in chunks]
        avg = round(sum(sizes) / max(len(sizes), 1), 1)
        cov = coverage(chunks, SAMPLE)
        print(f"[{name:11s}] n={len(chunks):3d}  avg={avg:5.1f}  max={max(sizes):4d}  min={min(sizes):3d}  coverage={cov}")
        rows.append({"strategy": name, "n": len(chunks), "avg_size": avg,
                     "max_size": max(sizes), "min_size": min(sizes), "coverage": cov})

    # 用 kbchat 的实现做交叉验证
    try:
        from kbchat.chunking import MarkdownHeadingSplitter, RecursiveSplitter, FixedCharSplitter
        kbchat_md = MarkdownHeadingSplitter().split_text(SAMPLE)
        kbchat_rec = RecursiveSplitter(chunk_size=200, chunk_overlap=30).split_text(SAMPLE)
        kbchat_fix = FixedCharSplitter(chunk_size=200, overlap=30).split_text(SAMPLE)
        print(f"\n[kbchat.md    ] n={len(kbchat_md)}")
        print(f"[kbchat.rec   ] n={len(kbchat_rec)}")
        print(f"[kbchat.fixed ] n={len(kbchat_fix)}")
        rows.append({"strategy": "kbchat_markdown", "n": len(kbchat_md), "kbchat": True})
        rows.append({"strategy": "kbchat_recursive", "n": len(kbchat_rec), "kbchat": True})
        rows.append({"strategy": "kbchat_fixed", "n": len(kbchat_fix), "kbchat": True})
    except Exception as e:
        print(f"\n⚠ kbchat 不可用（{e}），仅独立实现")

    # 写报告
    (ART / "comparison.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (ART / "comparison.md").write_text(
        "# L02 — Chunking 对比\n\n"
        "| strategy | n | avg_size | max_size | min_size | coverage |\n"
        "|---|---|---|---|---|---|\n"
        + "\n".join(
            f"| {r.get('strategy','')} | {r.get('n','-')} | {r.get('avg_size','-')} | {r.get('max_size','-')} | {r.get('min_size','-')} | {r.get('coverage','-')} |"
            for r in rows if not r.get("kbchat")
        )
        + "\n\n## 经验法则\n"
        "- **MD 文档** → MarkdownHeading（保 heading_path 利于过滤）\n"
        "- **聊天/邮件** → Recursive（按段落 / 句号 fallback）\n"
        "- **代码/固定 schema** → 按行/函数切，**不要用 Fixed**\n"
        "- **chunk_size** 经验：中文 200–400 字，英文 200–500 token\n"
        "- **overlap** 经验：10–20% chunk_size\n",
        encoding="utf-8",
    )
    print(f"\n✓ L02 完成。报告 → {ART / 'comparison.md'}")


if __name__ == "__main__":
    main()
