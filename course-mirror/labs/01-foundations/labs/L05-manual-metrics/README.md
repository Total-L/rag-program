# L05 — 手工检索指标

## 学习目标
- 手写 precision@k, recall@k, MRR, nDCG
- 知道每个指标的适用场景

## 跑
```bash
make lab-L05
```

## 公式速记
- **Precision@k** = |retrieved[:k] ∩ relevant| / k
- **Recall@k** = |retrieved[:k] ∩ relevant| / |relevant|
- **MRR** = 平均 1/rank_of_first_relevant
- **nDCG@k** = DCG@k / IDCG@k

## 何时用什么
- **Precision@k**：前 k 准不准（搜索排序）
- **Recall@k**：召回够不够（RAG 检索）
- **MRR**：单答案（FAQ）
- **nDCG@k**：多相关 + 排序敏感
