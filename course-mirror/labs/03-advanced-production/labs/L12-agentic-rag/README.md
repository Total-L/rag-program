# L12 — Agentic RAG

## 学习目标
- 看到 Self-RAG / CRAG / 朴素 Agentic 三种思路
- 看到"评估 → 改写 → 再检索"循环
- 理解 max_retries 和 cost guard 的必要性

## 跑
```bash
make lab-L12
```

## 3 模式对比
| 模式 | 思路 | 适用 |
|---|---|---|
| Self-RAG | LLM 自评"证据够吗" | 多步 QA、长答 |
| CRAG | 不够时改写 query 再检索 | 检索质量波动大 |
| 朴素 Agentic | 多步 tool use | 复杂多跳 |

## 流程
1. retrieve → 2. grade → 3. if poor: rewrite + retrieve → 4. generate

## 面试要点
- **Self-RAG** 论文关键：reflection tokens
- **CRAG** 用 Confidence 触发 3 个 action
- **Agentic** 风险：循环调用 → 需 max_retries + cost guard
