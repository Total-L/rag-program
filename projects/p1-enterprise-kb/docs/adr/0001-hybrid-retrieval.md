# ADR 0001 — Hybrid Retrieval（BM25 + dense + RRF）

## 状态
已采纳

## 背景
单一 dense 检索在精确词、专有名词、缩写场景下召回不足；单一 BM25 无法处理 paraphrase。

## 决策
1. **dense 通道**：Ollama nomic-embed-text，cosine top-8
2. **sparse 通道**：rank_bm25，top-8
3. **融合**：RRF，k=60，权重 equal
4. **重排后取 top-5**

## 后果
- ✅ Recall 提升 5–15 个点（实验观察）
- ✅ 不增加显著延迟（< 100ms）
- ⚠️ 索引存储：dense 向量 + BM25 pickle 双份
- ⚠️ BM25 重建在新增文档时是 O(N)；接受（200 份文档规模）

## 取舍
- **不用** Cohere Rerank（云 API，延迟 + 成本）
- **不用** ColBERT（v2 太重）
- **用** RRF 而非线性融合（更鲁棒，无需分数归一化）
