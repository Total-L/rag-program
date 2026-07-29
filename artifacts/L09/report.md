# L09 — 查询改写报告

## 4 策略

### 1. Multi-Query
- 让 LLM 生成 3 个 paraphrased query
- 适用：用户问题含糊或太短
- 成本：3× 检索 + 1× LLM

### 2. HyDE（Hypothetical Document Embeddings）
- 让 LLM 先生成'假想答案'，用其 embedding 检索
- 适用：知识领域与问题措辞差距大（如法律/医学）
- 风险：假想答案可能错，引入噪声

### 3. Step-Back
- 把具体问题抽象一层
- 适用：用户问细节但知识库里只有概览
- 思路：先检索概览 → 再检索细节

### 4. Decomposition
- 把多跳问题拆成子问题
- 适用：多跳 QA（multihop）
- 配合：可对每个子问题分别检索
