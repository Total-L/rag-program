# L07 — RRF 融合报告

## 公式
RRF(d) = Σ 1/(k + rank_i(d))，k 常取 60

## 观察
- 在两个 retriever 都命中的 doc，融合后分数叠加 → 排前
- 只有一个命中的 doc，分数弱 → 排后
- 权重偏置可调，但一般 equal 起点

## 面试要点
1. 为什么 RRF 而非线性融合（concat, max）？
2. RRF vs 加权倒数排名 vs CombSUM / CombMNZ？
3. 怎么知道 BM25 和 dense 哪个更重要？→ 调权重 + 评测
