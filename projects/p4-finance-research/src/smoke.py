"""P4 — Financial Research 烟雾测试。

简化：解析 markdown 表格 + 关键词检索 + 时间过滤。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np


def parse_table(md: str) -> list[dict]:
    """解析 markdown 表格。"""
    tables = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        if "|" in lines[i] and i + 1 < len(lines) and re.match(r"^[\s|:-]+$", lines[i + 1]):
            # header
            header = [c.strip() for c in lines[i].strip("|").split("|")]
            rows = []
            j = i + 2
            while j < len(lines) and "|" in lines[j]:
                row = [c.strip() for c in lines[j].strip("|").split("|")]
                rows.append(row)
                j += 1
            tables.append({"header": header, "rows": rows})
            i = j
        else:
            i += 1
    return tables


def table_to_text(table: dict) -> str:
    """把表格拍平成文本（便于嵌入）。"""
    out = []
    out.append(" | ".join(table["header"]))
    for r in table["rows"]:
        out.append(" | ".join(r))
    return "\n".join(out)


def fake_embed(text: str) -> np.ndarray:
    """⚠️ 这**不是**真实 embedding —— 是 bag-of-words 哈希桶，仅供烟雾测试。

    它把词哈希进 64 个桶再归一化，所以：
    - 只能做**词面重叠**匹配，没有任何语义能力（"营收"和"收入"完全不相关）
    - 换句话说，用它跑出来的"检索效果"没有参考价值，不能写进任何评测报告

    P4 的教学重点是**表格解析 + 时间过滤 + 引用溯源**，检索质量不是本项目的考察点，
    所以这里刻意不引入真实嵌入模型（省掉 Ollama 依赖，让 smoke test 能离线秒跑）。
    要评测检索质量请用 P1（真实 Ollama/BGE 嵌入 + RAGAS）。
    """
    v = np.zeros(64, dtype=np.float32)
    for w in text.split():
        v[hash(w) % 64] += 1
    return v / max(np.linalg.norm(v), 1e-9)


def search(docs: list[dict], query: str, k: int = 3, before: str | None = None) -> list[dict]:
    """before 过滤发布时间。"""
    if before:
        before_dt = datetime.fromisoformat(before)
        docs = [d for d in docs if datetime.fromisoformat(d["date"]) <= before_dt]
    qv = fake_embed(query)
    sims = []
    for d in docs:
        dv = fake_embed(d["text"])
        sims.append((d, float(dv @ qv)))
    sims.sort(key=lambda x: -x[1])
    return [d for d, _ in sims[:k]]


def main() -> None:
    print("════════════════════════════════════════════")
    print("  P4 — Financial Research 烟雾测试")
    print("════════════════════════════════════════════\n")

    fixtures = Path("datasets/fixtures/finance")
    if not (fixtures / "annual_reports.jsonl").exists():
        print(
            f"⚠ {fixtures}/annual_reports.jsonl 不存在，先跑：python -m datasets.generators.finance"
        )
        return

    # 加载
    docs = []
    for line in (fixtures / "annual_reports.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        docs.append(
            {
                "doc_id": d["doc_id"],
                "ticker": d["ticker"],
                "company": d["company"],
                "sector": d["sector"],
                "period": d["period"],
                "date": "2024-12-31",  # 简化为固定
                "text": d["content"],
            }
        )
    print(f"→ 加载 {len(docs)} 份年报")

    # 表格解析演示
    sample = docs[0]
    tables = parse_table(sample["text"])
    print(f"\n→ {sample['doc_id']} ({sample['company']}) 解析出 {len(tables)} 个表格")
    for t in tables[:1]:
        print(f"  header: {t['header']}")
        for r in t["rows"][:3]:
            print(f"    {r}")

    # 检索 + 时间过滤
    queries = [
        ("营收同比增长", None),
        ("科技行业 净利润", "2024-06-30"),  # 只看上半年
    ]
    for q, before in queries:
        print(f"\nQ: {q} (before={before})")
        hits = search(docs, q, k=2, before=before)
        for h in hits:
            print(f"  [{h['doc_id']}] {h['company']} ({h['sector']}) {h['period']}")

    # 写结果
    out = Path("artifacts/P4/smoke.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "n_docs": len(docs),
                "n_tables": sum(len(parse_table(d["text"])) for d in docs),
                "ok": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n✓ P4 完成 → {out}")


if __name__ == "__main__":
    main()
