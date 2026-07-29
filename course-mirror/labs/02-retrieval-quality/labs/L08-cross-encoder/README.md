# L08 — 交叉编码器重排

## 学习目标
- 看到 bi-encoder vs cross-encoder 差异
- 体验 cross-encoder 准但慢的特性
- 看到重排如何把 recall@5 提升 5–15 个点

## 跑
```bash
make lab-L08
```

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
- `cross-encoder/ms-marco-MiniLM-L-6-v2`：约 90MB，CPU 可跑
- `BAAI/bge-reranker-base` / `large`：更准但更慢
