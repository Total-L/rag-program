"""L06 — BM25 稀疏检索，与 dense 并排对比。

跑：
    python 02-retrieval-quality/labs/L06-bm25/run.py
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

ART = Path("artifacts/L06")
ART.mkdir(parents=True, exist_ok=True)

CORPUS = [
    "员工年假政策：司龄 1 年以下 5 天，1-3 年 10 天。",
    "病假需要 OA 申请，附医院证明。",
    "差旅住宿：一线城市 800 元，二线 600 元。",
    "VPN 接入：vpn.corp.example.com，绑定手机令牌。",
    "限制性股票 4 年 vest，cliff 1 年。",
    "P0 事故 30 分钟内 mitigation。",
    "发版：周二封板、周三灰度。",
    "用户 P0 反馈 1 小时响应。",
]


# ── 1. 简易 BM25（手写，加深理解）────────────────────────
class BM25:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs: list[list[str]] = []
        self.doc_lens: list[int] = []
        self.avg_dl: float = 0.0
        self.df: Counter = Counter()
        self.N: int = 0

    @staticmethod
    def tokenize(text: str) -> list[str]:
        # 中英混合：英文按词、中文按字
        tokens = re.findall(r"[A-Za-z]+|\d+|[一-鿿]", text)
        return [t.lower() for t in tokens]

    def fit(self, corpus: list[str]) -> None:
        self.docs = [self.tokenize(d) for d in corpus]
        self.doc_lens = [len(d) for d in self.docs]
        self.avg_dl = sum(self.doc_lens) / max(len(self.docs), 1)
        self.df = Counter()
        for d in self.docs:
            for term in set(d):
                self.df[term] += 1
        self.N = len(self.docs)

    def score(self, query: str) -> list[float]:
        q_tokens = self.tokenize(query)
        scores = [0.0] * self.N
        for q in q_tokens:
            df = self.df.get(q, 0)
            if df == 0:
                continue
            idf = math.log((self.N - df + 0.5) / (df + 0.5) + 1)
            for i, doc in enumerate(self.docs):
                tf = doc.count(q)
                if tf == 0:
                    continue
                dl = self.doc_lens[i]
                num = tf * (self.k1 + 1)
                den = tf + self.k1 * (1 - self.b + self.b * dl / self.avg_dl)
                scores[i] += idf * num / den
        return scores

    def topk(self, query: str, k: int = 3) -> list[tuple[int, float]]:
        s = self.score(query)
        idx = sorted(range(len(s)), key=lambda i: -s[i])[:k]
        return [(int(i), float(s[i])) for i in idx]


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L06 — BM25 稀疏检索")
    print("════════════════════════════════════════════\n")

    bm = BM25()
    bm.fit(CORPUS)

    queries = [
        "年假 几天",
        "P0 事故",
        "差旅 住宿",
        "vest 限制性股票",
    ]
    for q in queries:
        hits = bm.topk(q, k=3)
        print(f"\nQ: {q}")
        for idx, s in hits:
            print(f"  [{s:.2f}] {CORPUS[idx]}")

    # 与 rank_bm25 库对拍
    try:
        from rank_bm25 import BM25Okapi
        tokenized = [BM25.tokenize(d) for d in CORPUS]
        kb = BM25Okapi(tokenized, k1=1.5, b=0.75)
        print("\n→ rank_bm25 库验证:")
        for q in queries:
            scores = kb.get_scores(BM25.tokenize(q))
            idx = sorted(range(len(scores)), key=lambda i: -scores[i])[:3]
            print(f"  Q: {q} → top: {idx}")
    except Exception as e:
        print(f"\n⚠ rank_bm25 不可用：{e}")

    # 与 kbchat.BM25Index 对拍
    try:
        from kbchat.retrieval.bm25 import BM25Index
        from kbchat.models import Chunk, SourceRef
        idx = BM25Index()
        chunks = []
        for i, t in enumerate(CORPUS):
            src = SourceRef(
                source_path=f"corpus/{i}.md", note_title=f"doc{i}",
                heading_path=(), source_uri=f"obsidian://doc{i}", category="hr",
            )
            chunks.append(Chunk(
                chunk_id=f"c{i}", text=t, source=src,
                chunk_index=0, token_count=len(t)//4, content_hash=f"h{i}",
            ))
        idx.build(chunks)
        for q in queries[:1]:
            hits = idx.query(q, top_k=3)
            print(f"\n→ kbchat BM25Index 验证 Q: {q} → {[(h.chunk.chunk_id, h.score) for h in hits]}")
    except Exception as e:
        print(f"\n⚠ kbchat.BM25Index 不可用：{e}")

    (ART / "report.md").write_text(
        "# L06 — BM25 报告\n\n"
        "## 核心公式\n"
        "BM25(d, q) = Σ IDF(qi) · (tf(qi, d)·(k1+1)) / (tf(qi, d) + k1·(1-b+b·|d|/avgdl))\n\n"
        "## 与 dense 对比\n"
        "- **BM25 强项**：精确词匹配、缩写、型号、专有名词\n"
        "- **BM25 弱项**：paraphrase、同义词、跨语言\n"
        "- **dense 强项**：语义相似、paraphrase\n"
        "- **dense 弱项**：精确词、稀有词\n"
        "- **混合（hybrid）**：BM25 + dense + RRF 融合，几乎总是优于单一\n",
        encoding="utf-8",
    )
    print(f"\n✓ L06 完成。报告 → {ART / 'report.md'}")


if __name__ == "__main__":
    main()
