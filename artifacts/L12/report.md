# L12 — Agentic RAG 报告

## Self-RAG vs CRAG vs 朴素 Agentic

| 模式 | 思路 | 适用 |
|---|---|---|
| Self-RAG | LLM 自评「证据够吗」 | 多步 QA、长答 |
| CRAG | 不够时改写 query 再检索 | 检索质量波动大 |
| 朴素 Agentic | 多步 tool use | 复杂多跳 |

## 本 lab 演示
1. retrieve → 2. grade → 3. if poor: rewrite + retrieve → 4. generate

## 真 LangGraph 实现要点
- **3 节点** StateGraph: `retrieve` / `rewrite` / `generate`
- **条件边**: `add_conditional_edges("retrieve", route_fn, {"rewrite": ..., "generate": ...})`
- **回边**: `add_edge("rewrite", "retrieve")` —— 这是 LangGraph 比 LCEL 强的关键(LCEL 没有循环)
- **多轮记忆**: `messages` 字段用 `add_messages` reducer 累积;`MemorySaver` + `thread_id` 跨 invoke 持久化
- **历史传播**: retrieve 节点把上一轮 user message 拼到当前 query,让「那病假呢?」继承「年假」上下文
- **可观测性**: `app.get_graph().draw_mermaid()` 出 Mermaid 图
- **生产级扩展**: `MemorySaver` → `PostgresSaver` 即得跨进程 checkpoint + human-in-the-loop

## 面试要点
1. **Self-RAG** 论文关键：reflection tokens
2. **CRAG** 用 Confidence 触发 3 个 action：Correct/Incorrect/Ambiguous
3. **Agentic** 风险：循环调用 → 需 max_retries + cost guard
4. **LangGraph vs LCEL**: 流程图能画出来 = 该用 LangGraph;线性管道 = LCEL 够
5. **MemorySaver**: thread_id 隔离,生产换 PostgresSaver;可用于人机协同(interrupt_before)
