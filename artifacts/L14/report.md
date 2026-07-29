# L14 — Observability 报告

## 关键指标
- **p50/p95 延迟**（按 stage 拆分）
- **错误率**（按 error 类型）
- **trace 关联**（correlation_id 串联）

## kbchat 隐私策略
- trace **绝不记录** chunk 文本或 query 内容
- 仅记录 stage / duration / 错误类型 / doc_id 数量

## 面试要点
1. 链路追踪：OpenTelemetry / LangSmith / Langfuse
2. 采样策略：100% → 10% → 1%
3. 隐私红线：PII 不能进 trace
4. 告警阈值：p95 > 2s, error_rate > 5%
