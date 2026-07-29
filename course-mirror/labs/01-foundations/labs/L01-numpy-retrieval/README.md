# L01 — NumPy 手写余弦检索

## 学习目标
- 看到 Embedding 的本质 = 高维向量
- 理解余弦相似度的几何意义
- 看到 top-k 检索的最朴素实现
- 看到向量归一化（L2 norm → 内积即可）

## 跑
```bash
make lab-L01
```

## 关键代码点
- `toy_embed`：词袋 embedding（仅演示，**不是真语义**）
- `cosine_matrix`：X → 余弦矩阵
- `topk`：argsort 取前 k
- 实际 L2 归一化：`X / max(norm, 1e-9)`

## 面试题
1. 为什么 dense 检索通常 L2 归一化后用内积？
2. toy_embed 与真实 Embedding（如 BGE）差距在哪？
3. 100 万条文档的 O(N) 检索为何不能接受？HNSW/IVF 怎么解？

## 输出
- `artifacts/L01/cosine_matrix.npy` — 8×8 余弦矩阵
- `artifacts/L01/cosine_matrix.png` — 热力图
- `artifacts/L01/report.md` — 报告
