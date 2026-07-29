# L01 — NumPy 余弦检索 报告

## 关键观察
- 词袋 embedding 无法区分'请年假'与'休几天'这类 paraphrase（toy_embed 的局限）
- 余弦相似度对向量长度不敏感（已 L2 归一化）
- top-k 通过 argsort 即可，无需复杂索引（HNSW 是后话）

## 进阶思考（面试题）
1. 为什么 dense 检索通常 L2 归一化后用内积？
2. toy_embed 与真实 Embedding（如 BGE）差距在哪？
3. 100 万条文档的 O(N) 检索为何不能接受？HNSW/IVF 怎么解？
