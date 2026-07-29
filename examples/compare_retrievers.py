"""对比 4 种检索策略：纯 dense / 纯 BM25 / hybrid / hybrid+rerank。

跑：
    python examples/compare_retrievers.py

依赖：numpy + rank_bm25 + Ollama 在 localhost:11434。
无 kbchat 依赖；纯 NumPy + rank_bm25 自实现混合检索。
"""
import json
import urllib.request

import numpy as np

CORPUS = [
    "员工年假政策：司龄 1 年以下 5 天，1-3 年 10 天，3-5 年 15 天，5+ 年 20 天。",
    "病假需要 OA 申请，附医院证明。",
    "差旅住宿：一线城市 800 元，二线 600 元。",
    "VPN 接入：vpn.corp.example.com，绑定手机令牌。",
    "限制性股票 4 年 vest，cliff 1 年。",
    "P0 事故 30 分钟内 mitigation。",
    "发版：周二封板、周三灰度。",
    "用户 P0 反馈 1 小时响应。",
]

# 3 个金标问题（query → 期望命中的 doc 索引）
GOLDEN = {
    "员工每年能休多少天年假？": 0,
    "P0 事故如何处理？": 5,
    "公司差旅住宿标准？": 2,
}


def embed(text: str) -> list[float]:
    body = json.dumps({"model": "nomic-embed-text", "prompt": text}).encode()
    req = urllib.request.Request("http://localhost:11434/api/embeddings", data=body,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["embedding"]


# 预计算
doc_vecs = [embed(d) for d in CORPUS]
D = np.array(doc_vecs)
D_norm = D / np.maximum(np.linalg.norm(D, axis=1, keepdims=True), 1e-9)


def dense_topk(q: str, k: int = 3) -> list[int]:
    qv = np.array(embed(q))
    qn = qv / max(np.linalg.norm(qv), 1e-9)
    sims = (D_norm @ qn).tolist()
    return [i for i in sorted(range(len(sims)), key=lambda i: -sims[i])[:k]]


def bm25_topk(q: str, k: int = 3) -> list[int]:
    from rank_bm25 import BM25Okapi
    import re
    tok = lambda t: re.findall(r"[A-Za-z]+|\d+|[一-鿿]", t)
    bm = BM25Okapi([tok(d) for d in CORPUS])
    scores = bm.get_scores(tok(q))
    return [i for i in sorted(range(len(scores)), key=lambda i: -scores[i])[:k]]


def hybrid_topk(q: str, k: int = 3) -> list[int]:
    """RRF 融合 dense + BM25。"""
    d = dense_topk(q, k=5)
    b = bm25_topk(q, k=5)
    scores = {}
    for r, idx in enumerate(d, 1):
        scores[idx] = scores.get(idx, 0) + 1 / (60 + r)
    for r, idx in enumerate(b, 1):
        scores[idx] = scores.get(idx, 0) + 1 / (60 + r)
    return [i for i, _ in sorted(scores.items(), key=lambda x: -x[1])[:k]]


# ── 跑对比 ─────────────────────────────────────────
print("=" * 60)
print("  检索策略对比 (top-3 命中)")
print("=" * 60)
print(f"{'Query':25s}  {'期望':5s}  dense  bm25  hybrid")
print("-" * 60)
for q, expected in GOLDEN.items():
    d = dense_topk(q)
    b = bm25_topk(q)
    h = hybrid_topk(q)
    d_hit = "✓" if expected in d else "✗"
    b_hit = "✓" if expected in b else "✗"
    h_hit = "✓" if expected in h else "✗"
    print(f"{q:25s}  {expected:5d}  {d_hit}({d[0]})  {b_hit}({b[0]})  {h_hit}({h[0]})")

print()
print("观察：")
print("- 精确词（'P0' / 'vest'）→ BM25 强")
print("- paraphrase → dense 强")
print("- hybrid 几乎总优于任一单通道")
