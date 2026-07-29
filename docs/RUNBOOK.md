# RUNBOOK — rag_program 事故响应手册（ENTERPRISE_AUDIT.md 维度 6）

> **TL;DR**：on-call 工程师半夜被叫醒时按下面 6 个 incident 模板走。先 detect → diagnose → mitigate → verify；别直接动生产。

---

## 0. 应急前置

| 项 | 值 |
|---|---|
| Grafana 看板 | `https://grafana.<org>/d/rag-program`（运维自建） |
| Prometheus 端点 | `GET /v1/metrics` on every API pod |
| 日志查询 | `kubectl logs -l app=rag-program --tail=200 \| jq`（stdout 是 JSON） |
| trace_id 关联 | grep `<trace_id>` 同时在 metrics + structlog + audit 三个地方出现 |
| on-call 升级 | PagerDuty service `rag-program-api` |
| 文档维护 | 改本文件需 PR review + @oncall 批准 |

**应急前置 SOP**（任何 incident 都先做）：
1. 确认事故范围：哪些 tenant / 哪些 endpoint / 哪个时间窗
2. 拉最新 metric snapshot（Prometheus `instant query`）
3. 抽样 3-5 个 trace_id 关联日志 + audit
4. **不要直接重启服务**——先看 metric 区分是流量异常还是代码异常

---

## 1. Incident: Embedding 服务挂（Ollama / 嵌入 API 不可用）

**症状**
- `rag_query_latency_seconds` p95 飙到 30s+（retries 撑满）
- 日志 `嵌入失败（第 N 次）` 高频出现
- 摄取流水线 `dlq.jsonl` 写入量骤增

**Detect**
```promql
rate(rag_embed_retries_total[5m]) > 0.5   # 每秒重试 > 0.5 次
```

**Diagnose**
```bash
# 1. 验证服务连通
curl -fsS http://ollama-host:11434/api/tags
# 2. 看摄取重试计数
grep -c "重试" data/ingest.log | tail -1
# 3. 看 Ollama 资源
kubectl top pod -l app=ollama
```

**Mitigate**
1. 若 Ollama OOM → 扩容（horizontal）或降低 `OLLAMA_NUM_PARALLEL`
2. 若网络分区 → 切到备用 cluster（`OLLAMA_HOST` env 改 IP）
3. 摄取流水线自动 retry N 次后落 DLQ；**别**中断，让它继续跑 + 记 DLQ
4. 已经 in-flight 的 query 会自然 timeout；前端用 `rag_query_latency_seconds` 监控

**Verify**
- 5 分钟后 `rate(rag_embed_retries_total[5m])` 回到 0
- `rag_query_latency_seconds` p95 回归 baseline（见 `docs/SLO.md`）
- DLQ 增量停止

---

## 2. Incident: Chroma / pgvector 连接池满

**症状**
- `/v1/query` 返回 500，错误信息含 `pool exhausted` 或 `connection timeout`
- `rag_query_latency_seconds` p95 飙升但 `rag_embed_retries_total` 正常
- 同一时刻摄取也可能失败（共享 connection pool）

**Detect**
```promql
histogram_quantile(0.95, rate(rag_query_latency_seconds_bucket[5m])) > 10
# 伴随：
sum(rate(rag_queries_total{status="internal_error"}[5m])) > 0.1
```

**Diagnose**
```bash
# 1. 看 active connection
curl -s http://chroma-host:8000/api/v1/heartbeat
# 2. pgvector 用 pg_stat_activity
psql "$P6_PG_DSN" -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state"
# 3. 找 long-running query
psql "$P6_PG_DSN" -c "SELECT pid, query_start, state, query FROM pg_stat_activity WHERE state='active' ORDER BY query_start LIMIT 10"
```

**Mitigate**
1. 若 long query → `pg_terminate_backend(pid)`（**先确认是摄取，不是用户 query**）
2. 若连接池真满 → 临时调小 `top_k_final` / `top_k_dense`（减少 per-query 耗时）
3. 若频繁发生 → 改 pool size（chroma: `--workers`；pgvector: `pool_max_size`）

**Verify**
- 5 分钟后 active connection < pool_max_size / 2
- 错误率回归 baseline
- 没有新 long query

---

## 3. Incident: ACL 误过滤（用户看不到本该看到的文档）

**症状**
- 用户反馈"我能看到的文档怎么少了" / "权限变化后没生效"
- eval hit_rate 突然下降
- 不是租户隔离问题（X-Tenant-Id 正确）——是 sensitivity 过滤问题

**Detect**
```promql
# 用户能看到 chunk 数骤降
sum(rag_retrieval_hits_total) by (tenant_id)  # 比上周同期 -50%
```

**Diagnose**
```bash
# 1. 抽查该用户当前可见 chunk 数
python -c "
from acl import User
from index_store import P1IndexStore
u = User(user_id='alice', role='manager')
s = P1IndexStore(chroma_path='data/index')
print(len(s.fetch_for_user(allowed_sensitivities=['public','internal'], tenant_id='acme-corp')))
"
# 2. 比对同用户上周的可见数（git log diff / 索引重建 log）
# 3. 看 acl.can_see() 边界（internal: manager vs executive）
```

**Mitigate**
1. 若 role 配置错（yml / DB）→ 立即回滚 + 加缓存防 stale read
2. 若 chunk sensitivity 标签错（frontmatter 标错）→ 重跑 index_corpus
3. 若 Chroma metadata 漂移（upsert 时 metadata 没传 tenant_id / sensitivity）→ 重建索引
4. **不要**临时扩大 ACL 给用户——这是治标；先关 metric 告警 + 通知相关用户

**Verify**
- 该用户 `fetch_for_user()` 返回数 = 上周同期 ± 5%
- eval hit_rate 回归 baseline
- 24h 抽样确认无新误过滤

---

## 4. Incident: LLM JSON 解析失败（mmx / Ollama 返回格式错）

**症状**
- `rag_queries_total{status="abstained"}` 突然飙升
- 日志 `json_parsed: false` 高频出现
- 用户拿到拒答短语，但 evidence 列表非空（说明 retrieve 成功）

**Detect**
```promql
sum(rate(rag_queries_total{status="abstained"}[5m])) /
  sum(rate(rag_queries_total[5m])) > 0.3   # 拒答率 > 30%
```

**Diagnose**
```bash
# 1. 抽几条原始 LLM 输出
grep "llm_grade\|mmx\|ollama" logs/*.log | tail -20 | jq '.llm_response_text[:200]'
# 2. 看是不是 prompt 注入（用户输入污染 SYSTEM）
# 3. 看是不是模型升级导致 SYSTEM 不再有效
```

**Mitigate**
1. 若是 prompt 注入 → 立即启用 `_ABSTAIN_PHRASES` heuristic fallback（已内置）
2. 若是模型升级 → 临时回滚到上一个模型；调整 SYSTEM prompt
3. 若是 mmx 本身挂了 → 切到 ollama（`RAG_PROG_LLM_BACKEND=ollama`）；反之亦然
4. **不要**调整 `_ABSTAIN_PHRASES` 列表——那是兜底而不是诊断工具

**Verify**
- 拒答率 < 10%
- 抽样 10 条 query 确认答案非拒答短语
- eval 拒答率回归 baseline

---

## 5. Incident: Quota 突增（某租户配额提前耗尽）

**症状**
- `rag_queries_total{status="quota_exceeded"}` 飙升
- 该租户用户全部拿 429
- `_audit_write_total` 该 tenant 写入正常（说明审计没坏）
- 其他租户正常（不是系统级问题）

**Detect**
```promql
sum(rate(rag_queries_total{status="quota_exceeded"}[5m])) by (tenant_id) > 0.5
```

**Diagnose**
```bash
# 1. 看该租户的 quota 配置
grep -A 5 '"name": "acme-corp"' config/tenants.json
# 2. 看租户最近 query 模式（按 user_id 聚合）
cat data/audit/$(date -u +%Y-%m-%d).jsonl | \
  jq -r 'select(.tenant_id=="acme-corp") | .user_id_hash' | sort | uniq -c | sort -rn | head
# 3. 判断是 legitimate 流量增长 / 异常流量（同一 user 高频）/ 配额设小了
```

**Mitigate**
1. 配额设小 → 临时调大；通知用户 + 法务确认（合规审计要留痕）
2. 异常流量 → 临时 block 该 user_id（用 DSR endpoint 不能 block！—— 是抹除而非禁用；需另写 block endpoint，TODO S 档）
3. legitimate 增长 → 升级 plan（FREE → TEAM），与销售确认
4. **不要**改全局配额上限——只动该 tenant

**Verify**
- 30 分钟后 `quota_exceeded` 比率回到 < 1%
- 通知用户 + ticket 记录
- 若升级 plan → 改 `tenants.json` 并 reload registry

---

## 6. Incident: Tenant 注册表缺失（X-Tenant-Id 未注册）

**症状**
- `DELETE /v1/users/{user_id}/data` 或 `POST /v1/query` 返回 404 + `tenant not found`
- 客户端说"昨天还能用"
- 不是 header 格式问题（400）——是 tenant 真不存在

**Detect**
```promql
sum(rate(rag_queries_total{status="not_ready"}[5m]))   # 注意：404 走 HTTPException，不进 metrics；靠 access log
```

**Diagnose**
```bash
# 1. 看 tenants.json
cat config/tenants.json | jq '.tenants[].tenant_id'
# 2. 看最近是不是误删了
git log -p config/tenants.json | head -50
# 3. 看 audit 找该 tenant_id 的最近活动
ls data/audit/ | tail -3 | xargs cat | jq -r 'select(.tenant_id=="acme-corp") | .ts' | sort -u | tail
```

**Mitigate**
1. 误删 → 从 git 回滚 + reload registry（重启服务或加 SIGHUP 热加载，TODO）
2. 新租户上线未注册 → 走法务/合同流程，**不要**立即加注册表——审计要求签约在先
3. 租户主动弃用 → 走 DSR：保留 audit 90 天后 retention cleanup 自动删

**Verify**
- 该 tenant_id 在 `tenants.json` 出现
- reload 后 `GET /v1/healthz` 仍 200
- 用户能正常 query

---

## 7. 不在本手册范围的事故

下列情况升级到工程主管（不是 on-call 单独处理）：

- **数据泄漏 / GDPR 投诉** → 法务 + DPO；DSR 是技术动作，但合规定性走法务
- **LLM 输出违法内容**（歧视 / 性 / 政治敏感） → 法务 + 立刻 disable 该租户
- **PG / Chroma 数据损坏** → DBA；on-call 只做"保留现场 + 切只读"
- **K8s 节点 OOM / 整机重启** → 平台组；on-call 只看自己 service 的 readiness

---

## 8. Runbook 维护

每次 incident 复盘后：
- 把 detect / diagnose 命令加到对应章节
- 加 `promql` 表达式（运维值班方便复制）
- 更新"症状"匹配最新客户端反馈
- 若发现新 incident 类型 → 新增章节；不用 PR review 后立即生效（紧急）但事后补 review

文档 owner：on-call 轮值组；季度 review 一次完整度。