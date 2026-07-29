"""L08 — 交叉编码器重排。

跑：
    python 02-retrieval-quality/labs/L08-cross-encoder/run.py

原理：cross-encoder 把 (q, d) 拼起来送进 LLM，输出一个相关性分数，
比双塔（bi-encoder）准，但慢。一般只对 top-50 → top-5 用。
"""
from __future__ import annotations

import os
from pathlib import Path

ART = Path("artifacts/L08")
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


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L08 — 交叉编码器重排")
    print("════════════════════════════════════════════\n")

    # 用 kbchat 的 HeuristicReranker 作为无 ML baseline
    try:
        from kbchat.retrieval.reranker import HeuristicReranker, IdentityReranker
        # HeuristicReranker 期望 Candidate 对象，这里用其 score 逻辑演示
        from kbchat.models import Candidate
        candidates = [
            Candidate(doc_id=f"D{i}", text=d, score=0.5)
            for i, d in enumerate(CORPUS)
        ]
        heu = HeuristicReranker()
        rescored = heu.rerank("员工每年能休多少天年假？", candidates, top_k=5)
        print("→ kbchat.HeuristicReranker top5:")
        for c in rescored:
            print(f"  [{c.score:.3f}] {c.text[:50]}")
    except Exception as e:
        print(f"⚠ kbchat 不可用：{e}")

    # 尝试加载 cross-encoder（如已下载）
    try:
        from sentence_transformers import CrossEncoder
        model_name = os.environ.get("CROSS_ENCODER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
        ce = CrossEncoder(model_name, max_length=256)
        pairs = [("员工每年能休多少天年假？", d) for d in CORPUS]
        scores = ce.predict(pairs)
        ranked = sorted(zip(CORPUS, scores), key=lambda x: -x[1])[:5]
        print(f"\n→ cross-encoder ({model_name}) top5:")
        for d, s in ranked:
            print(f"  [{s:.3f}] {d[:50]}")
    except Exception as e:
        print(f"\n⚠ cross-encoder 未安装/未下载：{e}")
        print("  安装：pip install sentence-transformers")
        print("  首次会从 HuggingFace 下载约 90MB")

    (ART / "report.md").write_text(
        "# L08 — Cross-Encoder 重排报告\n\n"
        "## Bi-encoder vs Cross-encoder\n\n"
        "| | bi-encoder (dense) | cross-encoder |\n"
        "|---|---|---|\n"
        "| 输入 | q, d 分别编码 | (q, d) 一起编码 |\n"
        "| 速度 | 快（可预计算 d） | 慢（每对都要算） |\n"
        "| 准确 | 中 | 高 |\n"
        "| 适用 | top-1000 粗排 | top-50 精排 |\n\n"
        "## 实践\n"
        "1. retriever 召回 top-50\n"
        "2. cross-encoder 重排 top-5\n"
        "3. 预期 recall@5 提升 5–15 个点\n\n"
        "## 成本\n"
        "- ms-marco-MiniLM-L-6-v2：约 90MB，CPU 可跑\n"
        "- bge-reranker-base / large：更准但更慢\n",
        encoding="utf-8",
    )
    print(f"\n✓ L08 完成。报告 → {ART / 'report.md'}")


if __name__ == "__main__":
    main()
