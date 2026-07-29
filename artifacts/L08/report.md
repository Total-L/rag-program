# L08 — Cross-Encoder 重排报告

## Bi-encoder vs Cross-encoder

| | bi-encoder (dense) | cross-encoder |
|---|---|---|
| 输入 | q, d 分别编码 | (q, d) 一起编码 |
| 速度 | 快（可预计算 d） | 慢（每对都要算） |
| 准确 | 中 | 高 |
| 适用 | top-1000 粗排 | top-50 精排 |

## 实践
1. retriever 召回 top-50
2. cross-encoder 重排 top-5
3. 预期 recall@5 提升 5–15 个点

## 成本
- ms-marco-MiniLM-L-6-v2：约 90MB，CPU 可跑
- bge-reranker-base / large：更准但更慢
