# ADR 0002 — Cross-Encoder 重排

## 状态
已采纳（带 heuristic fallback）

## 背景
dense 检索 top-8 中有噪声，rerank 后能提升前 5 的精度。

## 决策
- 默认：`CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")`
- Fallback：`HeuristicReranker`（0.6 cosine + 0.4 lexical overlap，无 ML；P1 自实现，详见 `_compat.py` 与 ADR-0005）
- 切换：环境变量 `RAG_PROG_RERANKER=cross-encoder|heuristic`

## 后果
- ✅ Recall@5 提升 5–15 个点（实验观察）
- ✅ 完全本地，无云 API
- ⚠️ ms-marco 模型 90MB，首次需下载
- ⚠️ 跨编码器每次 query 慢 ~100ms（50 对比较）

## 取舍
- **不**用 BGE-reranker-large（更准但 3× 慢，磁盘 2GB）
- **不**用 LLM-as-reranker（贵 + 慢）
