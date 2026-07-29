"""L01 — NumPy 手写余弦检索。

教学目标：
1. 看到 Embedding 的本质 = 高维向量
2. 看到余弦相似度的几何意义
3. 看到 top-k 检索的最朴素实现
4. 看到向量归一化的影响（L2 norm → 内积即可）

运行：
    python 01-foundations/labs/L01-numpy-retrieval/run.py

输出：
- 打印前 5 条最相似的文档（按余弦）
- 生成 artifacts/cosine_matrix.npy + artifacts/scatter.png
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ART = Path("artifacts/L01")
ART.mkdir(parents=True, exist_ok=True)


# ── 1. 准备 8 条玩具语料 ──────────────────────────────────────
CORPUS = [
    "员工年假政策：司龄 1 年以下 5 天，1-3 年 10 天。",
    "病假需要 OA 申请，附医院证明。",
    "差旅报销：住宿一线 800 元/晚，二线 600 元。",
    "VPN 接入：vpn.corp.example.com，首次需绑定手机令牌。",
    "限制性股票分 4 年 vest，cliff 1 年。",
    "P0 事故 30 分钟内 mitigation，24 小时 RCA。",
    "发版流程：周二封板、周三灰度 10%、周五 100%。",
    "用户反馈 P0 1 小时响应。",
]


# ── 2. 一个 toy embedding：词袋 + 长度归一化 ─────────────────
def toy_embed(texts: list[str], vocab: dict[str, int]) -> np.ndarray:
    """把每条文本映射为词频向量（演示用，不是真正的语义）。"""
    dim = len(vocab)
    M = np.zeros((len(texts), dim), dtype=np.float32)
    for i, t in enumerate(texts):
        for word in t:
            j = vocab.get(word)
            if j is not None:
                M[i, j] += 1.0
    return M


def build_vocab(corpus: list[str]) -> dict[str, int]:
    vocab: dict[str, int] = {}
    for t in corpus:
        for ch in t:
            if ch not in vocab and ("一" <= ch <= "鿿" or ch.isalnum()):
                vocab[ch] = len(vocab)
    return vocab


# ── 3. 余弦相似度矩阵 ─────────────────────────────────────
def cosine_matrix(X: np.ndarray) -> np.ndarray:
    """X: (n, d) → (n, n) 余弦相似度。"""
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    X_norm = X / np.maximum(norms, 1e-9)
    return X_norm @ X_norm.T


# ── 4. top-k 检索 ──────────────────────────────────────────
def topk(query_vec: np.ndarray, doc_vecs: np.ndarray, k: int = 3) -> list[tuple[int, float]]:
    norms_q = np.linalg.norm(query_vec)
    norms_d = np.linalg.norm(doc_vecs, axis=1)
    sims = (doc_vecs @ query_vec) / np.maximum(norms_q * norms_d, 1e-9)
    idx = np.argsort(-sims)[:k]
    return [(int(i), float(sims[i])) for i in idx]


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L01 — NumPy 手写余弦检索")
    print("════════════════════════════════════════════\n")

    vocab = build_vocab(CORPUS)
    print(f"vocab size: {len(vocab)}")

    # embedding
    X = toy_embed(CORPUS, vocab)
    print(f"X shape: {X.shape}")

    # 全相关矩阵
    S = cosine_matrix(X)
    np.save(ART / "cosine_matrix.npy", S)
    print(f"cosine matrix:\n{np.round(S, 2)}\n")

    # 检索演示
    queries = [
        "我想请年假，休几天？",
        "P0 事故如何处理？",
        "笔记本电脑怎么申请？",
    ]
    for q in queries:
        qv = toy_embed([q], vocab)[0]
        hits = topk(qv, X, k=3)
        print(f"Q: {q}")
        for idx, score in hits:
            print(f"  [{score:.3f}] {CORPUS[idx]}")
        print()

    # 简单可视化（如果 matplotlib 可用）
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(6, 6))
        im = ax.imshow(S, cmap="viridis")
        ax.set_xticks(range(len(CORPUS)))
        ax.set_yticks(range(len(CORPUS)))
        ax.set_xticklabels([f"D{i}" for i in range(len(CORPUS))], rotation=45)
        ax.set_yticklabels([f"D{i}" for i in range(len(CORPUS))])
        for i in range(len(CORPUS)):
            for j in range(len(CORPUS)):
                ax.text(j, i, f"{S[i, j]:.1f}", ha="center", va="center", color="w", fontsize=7)
        ax.set_title("Cosine similarity (toy embeddings)")
        plt.colorbar(im, ax=ax)
        plt.tight_layout()
        plt.savefig(ART / "cosine_matrix.png", dpi=120)
        print(f"✓ saved {ART / 'cosine_matrix.png'}")
    except ImportError:
        print("⚠ matplotlib 未安装，跳过可视化")

    # 写报告
    (ART / "report.md").write_text(
        "# L01 — NumPy 余弦检索 报告\n\n"
        "## 关键观察\n"
        "- 词袋 embedding 无法区分'请年假'与'休几天'这类 paraphrase（toy_embed 的局限）\n"
        "- 余弦相似度对向量长度不敏感（已 L2 归一化）\n"
        "- top-k 通过 argsort 即可，无需复杂索引（HNSW 是后话）\n\n"
        "## 进阶思考（面试题）\n"
        "1. 为什么 dense 检索通常 L2 归一化后用内积？\n"
        "2. toy_embed 与真实 Embedding（如 BGE）差距在哪？\n"
        "3. 100 万条文档的 O(N) 检索为何不能接受？HNSW/IVF 怎么解？\n",
        encoding="utf-8",
    )
    print(f"✓ L01 完成。报告 → {ART / 'report.md'}")


if __name__ == "__main__":
    main()
