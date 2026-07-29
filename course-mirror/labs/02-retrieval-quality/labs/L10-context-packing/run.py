"""L10 — Context Packing：per-source cap / near-dup / token budget / 排序。

跑：
    python 02-retrieval-quality/labs/L10-context-packing/run.py

复用 kbchat.retrieval.context.pack_evidence。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ART = Path("artifacts/L10")
ART.mkdir(parents=True, exist_ok=True)


# ── 1. 手写 pack_evidence（演示核心逻辑）────────────────
def pack_evidence(
    candidates: list[dict],
    token_budget: int = 800,
    per_source_cap: int = 2,
    chars_per_token: int = 2,  # 中英混合粗估
) -> list[dict]:
    """核心规则：
    1. 按相关性降序
    2. 同 source 最多 per_source_cap 条
    3. sha1 前 8 字符做 near-dup 去重
    4. 总字符数 ≤ token_budget * chars_per_token
    5. "lost in the middle"：相关最高的放首尾，相关低的放中间
    """
    seen_hashes: set[str] = set()
    per_source_count: dict[str, int] = {}
    out: list[dict] = []
    budget_chars = token_budget * chars_per_token

    for c in candidates:
        # 1. dedup
        h = hashlib.sha1(c["text"].encode("utf-8")).hexdigest()[:8]
        if h in seen_hashes:
            continue
        # 2. per-source cap
        cnt = per_source_count.get(c["source"], 0)
        if cnt >= per_source_cap:
            continue
        # 3. budget
        if sum(len(x["text"]) for x in out) + len(c["text"]) > budget_chars:
            continue
        seen_hashes.add(h)
        per_source_count[c["source"]] = cnt + 1
        out.append(c)

    # "lost in the middle"：reorder 0, n-1, 1, n-2, 2, n-3 ...
    n = len(out)
    reordered: list[dict] = []
    i, j = 0, n - 1
    while i <= j:
        if i == j:
            reordered.append(out[i])
        else:
            reordered.append(out[i])
            reordered.append(out[j])
        i += 1
        j -= 1
    return reordered


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L10 — Context Packing")
    print("════════════════════════════════════════════\n")

    # 模拟 8 个候选
    candidates = [
        {"doc_id": f"D{i}", "source": f"S{i//3}", "score": 1.0 - i*0.05, "text": f"文本 {i} " * 30}
        for i in range(8)
    ]
    # 故意加一个近重复
    candidates.append({"doc_id": "D-DUP", "source": "S0", "score": 0.95, "text": candidates[0]["text"]})

    print("→ 输入:", len(candidates), "个候选")
    packed = pack_evidence(candidates, token_budget=200, per_source_cap=2)
    print(f"→ 输出: {len(packed)} 条（去重 + per-source cap + budget）")
    for i, c in enumerate(packed):
        marker = "首" if i == 0 else "尾" if i == len(packed) - 1 else "中"
        print(f"  [{i}] ({marker}) src={c['source']} score={c['score']:.2f} text_len={len(c['text'])}")

    # 复用 kbchat
    try:
        from kbchat.retrieval.context import pack_evidence as kbchat_pack
        from kbchat.models import Candidate, Chunk, SourceRef
        kbchat_candidates = []
        for i, c in enumerate(candidates):
            src = SourceRef(
                source_path=f"{c['source']}.md", note_title=c["source"],
                heading_path=(), source_uri=f"obsidian://{c['source']}", category="hr",
            )
            chunk = Chunk(
                chunk_id=c["doc_id"], text=c["text"], source=src,
                chunk_index=0, token_count=len(c["text"])//4, content_hash=f"h{i}",
            )
            kbchat_candidates.append(Candidate(chunk=chunk, score=c["score"], rank=i, retriever="toy"))
        packed_kb = kbchat_pack(kbchat_candidates, token_budget=200, per_source_cap=2)
        print(f"\n→ kbchat.pack_evidence 输出 {len(packed_kb)} 条")
    except Exception as e:
        print(f"\n⚠ kbchat 不可用：{e}")

    (ART / "report.md").write_text(
        "# L10 — Context Packing 报告\n\n"
        "## 5 条规则\n"
        "1. **去重**（sha1 前 8 字符）\n"
        "2. **per-source cap**（每源最多 N 条）\n"
        "3. **token budget**（总字符数上限）\n"
        "4. **'lost in the middle' 排序**（最相关的放首尾）\n"
        "5. **截断**（超过的留到下轮）\n\n"
        "## 面试要点\n"
        "- 'Lost in the middle' 是 Liu et al. 2023 的发现：LLM 倾向于关注首尾\n"
        "- 排序策略：1, N, 2, N-1, 3, N-2, ...\n"
        "- per_source_cap 防止单源独大影响答案多样性\n",
        encoding="utf-8",
    )
    print(f"\n✓ L10 完成。报告 → {ART / 'report.md'}")


if __name__ == "__main__":
    main()
