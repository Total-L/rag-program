# L05 — 手工指标报告

## 公式速记
- **Precision@k** = |retrieved[:k] ∩ relevant| / k
- **Recall@k** = |retrieved[:k] ∩ relevant| / |relevant|
- **MRR** = 平均 1/rank_of_first_relevant
- **nDCG@k** = DCG@k / IDCG@k（考虑位置权重）

## 何时用什么
- **Precision@k**：关心前 k 准不准（如搜索排序）
- **Recall@k**：关心召回够不够（如 RAG 检索）
- **MRR**：单答案场景（FAQ）
- **nDCG@k**：多相关 + 排序敏感
