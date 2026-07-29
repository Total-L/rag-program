# L07 — RRF 融合 + 权重调参

## 学习目标
- 看到 RRF（Reciprocal Rank Fusion）公式
- 看到权重偏置的影响

## 跑
```bash
make lab-L07
```

## 公式
```
RRF(d) = Σ 1/(k + rank_i(d))，k 常取 60
```

## 面试要点
1. 为什么 RRF 而非线性融合（concat, max）？
2. RRF vs 加权倒数排名 vs CombSUM / CombMNZ？
3. 怎么知道 BM25 和 dense 哪个更重要？→ 调权重 + 评测
