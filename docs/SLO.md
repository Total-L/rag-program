# SLO — rag_program 服务等级目标（ENTERPRISE_AUDIT.md 维度 6）

> **TL;DR**：p50 < 1.5s / p95 < 5s / p99 < 15s；月可用性 99.5%；月错误率 < 1%；配额限流属"客户端问题"不进 SLO。

---

## 1. SLO 矩阵（核心 4 项）

| SLO | 目标 | 错误预算（月） | 测量窗口 | 当前 baseline | 改进目标 |
|---|---|---|---|---|---|
| **Latency p50** | ≤ 1.5s | — | 28 天滚动 | 0.8s (eval golden) | 0.6s |
| **Latency p95** | ≤ 5s | 200s/月 (0.07%) | 28 天滚动 | 2.4s (eval golden) | 2.0s |
| **Latency p99** | ≤ 15s | 50s/月 (0.02%) | 28 天滚动 | 5.5s (eval golden, 1 离群) | 5.0s |
| **Availability** | ≥ 99.5% | 3.6h/月 (0.5%) | 28 天滚动 | 100% (没真生产) | 99.5% |
| **Error Rate** (non-quota) | ≤ 1% | — | 28 天滚动 | < 0.1% (eval) | < 0.5% |

**错误预算消耗过半** → 触发"feature freeze"，新功能 PR 等下一窗口再合。

**为什么 p95 而不是 p99 定 5s**：
- 多数 RAG query 是 LLM 调用主导（>70% 总耗时），LLM p95 抖动 ~2x 正常
- 设 p99 ≤ 5s 等于要求 LLM 不能抖，违背物理规律
- p95 设紧（5s）让用户体验稳，p99 设宽（15s）留 buffer

---

## 2. 指标定义与 PromQL

### 2.1 Latency

```promql
# p50 / p95 / p99（按 tenant_id 分桶）
histogram_quantile(0.50, sum(rate(rag_query_latency_seconds_bucket[5m])) by (le, tenant_id))
histogram_quantile(0.95, sum(rate(rag_query_latency_seconds_bucket[5m])) by (le, tenant_id))
histogram_quantile(0.99, sum(rate(rag_query_latency_seconds_bucket[5m])) by (le, tenant_id))

# 全局 p95（跨租户）
histogram_quantile(0.95, sum(rate(rag_query_latency_seconds_bucket[5m])) by (le))
```

**注意**：`histogram_quantile` 是估算值；30 天统计应配合 Grafana 折线图肉眼确认趋势。

### 2.2 Availability

定义：API pod 收到合法请求 → 200/4xx 响应的比例。5xx 与超时不算。

```promql
# 成功率（200 + 4xx 都算"成功服务"）
sum(rate(rag_queries_total[28d])) - sum(rate(rag_queries_total{status="internal_error"}[28d]))
/
sum(rate(rag_queries_total[28d]))
```

**为什么 4xx 算成功**：
- 4xx = 客户端问题（参数错、配额超限、tenant 未注册）；不算 API 故障
- 5xx + 超时 = 服务侧问题，才计入 SLO

### 2.3 Error Rate

定义：`status="internal_error"` + `status="graph_no_attempt"` 占总查询的比例。

```promql
sum(rate(rag_queries_total{status=~"internal_error|graph_no_attempt"}[28d]))
/
sum(rate(rag_queries_total[28d]))
```

**为什么 quota_exceeded 不算 error**：
- 配额耗尽 = 客户端使用方式问题（不是系统故障）
- 计入会让 SLO 在流量高峰虚高，掩盖真问题

### 2.4 Throughput（参考，非 SLO）

```promql
sum(rate(rag_queries_total[5m])) by (tenant_id)
```

企业 KB 助手典型场景：每个租户 ~10-50 QPS 高峰，平均 < 5 QPS。

### 2.5 Audit 写入健康

```promql
# audit 写失败率
sum(rate(rag_audit_write_total{status="failure"}[5m]))
/
sum(rate(rag_audit_write_total[5m]))
```

目标：< 1%；超过立即告警（详见 `RUNBOOK.md §1` 类比——audit 是合规不能丢）。

---

## 3. 排除窗口（不算 SLO）

下列情况产生的错误/超时**不计入 SLO**：

- **租户配额耗尽**（`status="quota_exceeded"`）——客户端行为
- **tenant_id 未注册**（HTTP 404）——客户端配置错
- **依赖主动维护**：预先通知的 Ollama / Chroma / pgvector 升级窗口
- **DoS / 异常流量**：被 rate limiter 拒绝的请求

**记录方式**：维护窗口用 Prometheus 标签 `maintenance=true` 或单独 metric `rag_maintenance_window_active`。

---

## 4. 错误预算策略

### 4.1 预算消耗过半（>50%）

1. 当前 sprint 不合新功能 PR（feature freeze）
2. 优先修复已知的 latency/error root cause
3. 通知工程主管

### 4.2 预算消耗到 80%

1. 立即冻结非关键 PR
2. 启动 incident review（即使没真事故）
3. 重设 SLO 目标（若 baseline 抬升，相应调高）

### 4.3 预算耗尽（>100%）

1. 立即 incident postmortem
2. 冻结所有 PR 直到下个窗口
3. SLO 目标检讨：是否需调高 / 是否基础设施问题

---

## 5. 告警规则（基于 SLO）

| 告警 | 条件 | 严重度 | 通知 |
|---|---|---|---|
| Latency p95 SLO 燃尽速率过快 | `(current_p95 / target_p95) > 1.5` for 10m | warning | Slack `#oncall` |
| Latency p99 > 15s | `histogram_quantile(0.99, ...) > 15` for 5m | critical | PagerDuty |
| Availability < 99% | 见 §2.2 PromQL，< 99% for 10m | critical | PagerDuty |
| Error rate > 1% | 见 §2.3，> 1% for 10m | critical | PagerDuty |
| Audit write failure > 1% | 见 §2.5，> 1% for 5m | critical | PagerDuty |
| Quota exhausted (> 5%) | `status="quota_exceeded"` > 5% for 30m | info | Slack `#sales` |

---

## 6. 当前 baseline（来自 `eval/reports/p1_golden.json`）

| 指标 | 当前值 | SLO 目标 | 距离 |
|---|---|---|---|
| p50 latency | 0.8s | 1.5s | ✅ 47% 余裕 |
| p95 latency | 2.4s | 5s | ✅ 52% 余裕 |
| p99 latency | 5.5s (含 1 离群) | 15s | ✅ 63% 余裕 |
| Hit rate | 0.0 (eval 门**未真卡住**) | n/a | 见 §7 |
| Eval golden pass | 50/50 (单元) | 50/50 | ✅ |

**注意**：`eval/reports/p1_golden.json` 的 hit_rate=0 是历史 bug（详见 RCA_p1_eval_regression.md），不反映真实质量。本 SLO 文件的 latency baseline 来自同次跑的分位数据，但**未**重新跑 eval 验证 hit_rate。

---

## 7. 改进路线图（按 SLO 重要性）

| 优先级 | 项 | 影响 | 估计工作量 |
|---|---|---|---|
| P0 | 真接 RAGAS eval，让 hit_rate 真卡住 | hit_rate SLO 才能定义 | 1 周 |
| P0 | retry / circuit breaker（fail fast） | p99 latency | 1 周 |
| P1 | pgvector 索引调优（IVFFlat lists） | p95 latency | 3 天 |
| P1 | LLM response streaming（SSE） | p50 latency（用户感知） | 1 周 |
| P2 | embedding 预计算（异步队列） | 摄取吞吐 | 1 周 |
| P2 | 跨租户 metric 隔离的 SLO dashboard | 故障定位 | 3 天 |

**P0 + P1 完成可把 p95 从 2.4s 拉到 ≤ 1.5s；P2 主要影响吞吐而非延迟。**

---

## 8. SLO 维护

- **季度 review**：调目标值（baseline 漂移时）；不在 SLO 内的小项不要塞进来
- **架构变更必 review SLO**：换 LLM / 换向量库 / 加中间层 → 重测 latency baseline
- **SLO 与 RUNBOOK 联动**：新 SLO 必须有对应 RUNBOOK incident（否则告警无人响应）

文档 owner：SRE；变更需 PR review + 工程主管批准。