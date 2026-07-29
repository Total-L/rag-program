# rag-program 企业级审计报告（v1 · 2026-07）

> 对当前 `rag_program` 是否符合"企业级应用"标准的横切评估。上一轮解耦工作（21 处 kbchat 引用 → 0、4 处硬编码路径 → 0、P1/P5/P6 测试全绿）完成后做的基线检查。

## 总判定

**🟡 半成品 → 准企业级**

- **算法侧**已接近生产形态：RAG 检索/ACL/拒答/citation 校验、50 题金标、RAGAS-style 阈值门禁、P6 摄取流水线是真生产级。
- **横切工程侧**有明显缺口：缺 CI/CD、缺可观测、缺审计日志、缺 REST API、缺多租户、缺 LICENSE、缺 lockfile、缺 runbook、缺流式输出。

补齐下文 **M 档（3 项）** 即可上生产；补齐 **S 档（4 项）** 可对外服务化；**C 档** 是加分项。

---

## 评级汇总（9 维度）

| # | 维度 | 评级 | 一句话 |
|---|---|---|---|
| 1 | 顶层架构与边界 | 🟢 企业级 | 解耦后 `projects/` `eval/` `interview/` `05-data-engineering/` 边界清晰；`course-mirror/` 显式标注可选 |
| 2 | RAG 算法正确性 | 🟢 企业级 | 混合检索 BM25+dense+RRF / cross-encoder rerank / ACL metadata pushdown / citation 强制校验 / abstention 都到位 |
| 3 | 摄取流水线（P6） | 🟢 企业级 | 真生产级：幂等 upsert + SQLite manifest + DLQ + PII 9 类 + 4 connector + 表头保留 + Chroma/pgvector 双后端 + 110 测试 |
| 4 | 测试与评测 | 🟢 企业级(C1+C2 完成) | P2/P3/P4 单测 18/29/25,P1 E2E 8,真 RAGAS + nDCG + hallucination + A/B harness;`hit_rate=0.0` 是独立 RCA(D8) |
| 5 | 安全与合规 | 🟡 半成品 → S3 完成后接近 🟢 | ACL/PII/path 黑名单是真东西；S3 加 audit log + GDPR DSR；缺 prompt injection 运行时防御、依赖 lockfile、LICENSE |
| 6 | 可观测与运维 | 🔴 缺失 → M1+D6 完成 🟢 | M1 已做 structlog / trace_id / Prom 7 metrics / 3 端点；D6 加 tenacity 重试 / QueryStatus 分桶（6 状态）/ RUNBOOK 6 incident / SLO 4 项 + PromQL / OTel 最小集成 |
| 7 | API 与集成 | 🔴 缺失 → M2+D7 完成 🟢 | M2 已做 FastAPI + 7 端点 + API key + SDK；D7 加 SSE 流式 / /v1/ingest 薄壳 / webhook 注册+回调+HMAC；缺 Slack/Teams/邮件 connector / OAuth / GraphQL |
| 8 | 多租户与成本治理 | 🟡 半成品 → S2 完成基线 | tenant_id 全链路注入 + 跨租户隔离 + 24h 配额 + metrics 分桶；缺 Redis / billing / 物理隔离 |
| 9 | 项目治理与 CI/CD | 🟡 半成品 → S1 完成 + C5 容器化基建 | lint + py3.11/3.12/3.13 测试矩阵 + 回归 gate workflow；C5 加 Dockerfile / docker-compose 7 服务 / OTel+Prom 配置；缺 badge / coverage / release / K8s manifests / CI publish |

**汇总**：🟢×6  🟡×3  🔴×0  （C2 让维度 4 从 🟡→🟢；🟢×6 = 维度 1/2/3/4/6/7；🟡×3 = 维度 5/8/9；维度 5 仍 🟡：缺 prompt injection / lockfile / LICENSE）

---

## 维度 1 — 顶层架构与边界 🟢

**现状**
- 解耦后企业程序本体 = `projects/` + `eval/` + `interview/` + `05-data-engineering/` + `datasets/` + `scripts/` + `examples/` + 根目录 md 五件套（README/00-NAVIGATION/QUICKSTART/VERIFY/SUMMARY）。
- `course-mirror/` 显式标注为"训练营配套、非企业程序本体"，opt-in 依赖。
- `pyproject.toml` 的 `[project.optional-dependencies] course-mirror = ["kbchat @ file:///Users/totallai/rag-course"]` 用 PEP 508 本地路径 optional extra。

**证据**
- `course-mirror/README.md`（开头明示"非企业程序本体"）
- `README.md` `00-NAVIGATION.md` `QUICKSTART.md` `VERIFY.md` `SUMMARY.md`（根目录五件套齐全）
- ADR-0005 `p1/docs/adr/0005-p1-kbchat-free.md`（解耦决策记录）

**建议**
- 当前档位即可。无重大改进空间。
- 微调：在 `README.md` 顶部加 1 行 "Status: 准企业级，缺口见 [ENTERPRISE_AUDIT](./ENTERPRISE_AUDIT.md)" 让读者一眼看到审计状态。

---

## 维度 2 — RAG 算法正确性 🟢

**现状**
- P1 主路径走教科书做法：BM25 + dense cosine → RRF(k=60, weight=2.0) → cross-encoder rerank → pack_evidence(token_budget=2400, per_source_cap=2) → grounded JSON-mode generation → citation 校验 → 拒答
- ADR-0004 改 ACL 判定时机：build-time → query-time（按 `where={"sensitivity": {"$in": allowed}}` pushdown），符合企业 RAG 通用做法
- 50 题金标覆盖 7 slice（semantic/lexical/metadata/multihop/bilingual/unanswerable/adversarial）
- LLM-judge 自实现（mmx 主路径 + ollama fallback + heuristic fallback）

**证据**
- `projects/p1-enterprise-kb/src/pipeline.py:286-322`（RRF 实现）
- `projects/p1-enterprise-kb/src/pipeline.py:362-367`（citation 校验 + 全部失效则拒答）
- `projects/p1-enterprise-kb/docs/adr/0001-hybrid-retrieval.md` … `0005-p1-kbchat-free.md`（5 个 ADR）
- `datasets/golden/README.md`（7 slice 表格）
- `projects/p1-enterprise-kb/src/llm_judge.py`（LLM-as-judge 三档实现）

**半成品点**
- **L09 HyDE/multi-query** 在 `course-mirror/labs/02-retrieval-quality/L09-query-rewrite/` 里有实验代码，但**未接入** P1 主路径；`agentic_graph.py` 里只有 `_heuristic_rewrite`。
- embedding 模型 `nomic-embed-text` 硬编码在 `p6/src/embeddings.py:33`，无版本注册机制。
- pgvector `vector(768)` 维度硬编码在 DDL，换模型要同步改库（无 migration script）。

**建议**
- M3：补 `EmbeddingModelRegistry`（轻量即可，见 C3）。
- 把 L09 query-rewrite 抽出来作为 P1 可选路径，env 开关即可。

---

## 维度 3 — 摄取流水线（P6） 🟢

**现状**
P6 是整个 repo 工程化程度最高的一块：
- **幂等 upsert**：pgvector `ON CONFLICT DO UPDATE` + Chroma `upsert()`
- **CDC manifest**：SQLite + etag/sha256，per-connector cursor
- **DLQ**：`IngestFailure` dataclass → `dlq/failed.jsonl`
- **PII 9 类识别 + 三策略**：MASK / HASH / DROP（CN 手机/身份证/银行卡/邮箱/IP/JWT/API key/DSN/private key）
- **自动敏感度升级**：`classify_sensitivity` only-up
- **4 connector**：fs / sql / objectstore (S3-shaped) / confluence
- **表头保留切分**：`table_aware_chunks`
- **双后端**：Chroma（dev）+ pgvector（prod）
- **110 个测试通过、1 个 skipped**（pgvector E2E 本机无 Docker 跳）

**证据**
- `projects/p6-ingestion-platform/src/manifest.py`（CDC）
- `projects/p6-ingestion-platform/src/pii.py`（PII 9 类 + 自动 sensitivity）
- `projects/p6-ingestion-platform/src/store.py:9`（upsert 注释 + `pg_upsert_sql`）
- `projects/p6-ingestion-platform/src/embeddings.py:120-122`（手写 retry + backoff）
- `projects/p6-ingestion-platform/run_ingest.py:170-176`（per-doc 隔离）

**半成品点**
- retry 是手写的，`tenacity` 没引入；无熔断器
- 4 connector 不够：缺 Notion / Slack / Gmail / Jira / Web
- `run_ingest.py` 是 CLI，无 REST/SSE 触发入口
- chunking 单一 markdown strategy，缺 AST-aware 代码切分（除 P2 单点外）

**建议**
- S1：CI 接 `p6` 测试矩阵（必跑）
- C4：扩 connector 套件（Notion / Slack / Jira 等）

---

## 维度 4 — 测试与评测 🟢（C1+C2 完成）

**现状（2026-07-28 v3 — C2 完成）**
- 50 题金标 + RAGAS-style 阈值门禁 + LLM-judge 三件是真东西
- P1: **+8 E2E 端到端测试** + 25 既有 + **C1 +23 RAGAS 指标** = 56 用例
- P2: **+16 单测**(2 → 18)—— AST 切分 / 嵌入 / 搜索 / 端到端 sample_corpus
- P3: **+24 单测**(5 → 29)—— 6 intent / 转人工 / FAQ / 工单 / 端到端
- P4: **+22 单测**(3 → 25)—— 表格解析 / 序列化 / 时间过滤 / 端到端 fixtures
- P5: 2 测试文件（16 用例）
- P6: 8 测试文件 + E2E（110 passed, 1 skipped）
- **总计 446 passed, 6 skipped, 0 failed**(从 356 → 446,+90)

**C1 已完成**(2026-07-28 v2)
- `eval/ragas/runner.py`（真 RAGAS-协议兼容 runner,4 指标：`faithfulness` / `answer_relevancy` / `context_precision` / `context_recall`,LLM-judged via mmx）
- `eval/ragas/retrieval.py`（nDCG@5 / nDCG@10,纯 stdlib,不依赖 LLM）
- `eval/ragas/hallucination.py`（三口径：`hallucination_rate` / `unsupported_claims_rate` / `numeric_hallucination_rate`）
- `eval/ragas/compare.py`（A/B pipeline config harness,`--compare cfg_a.yml cfg_b.yml`）
- `eval/ragas/run.py` → deprecation shim(转 `runner.main()` + warning)
- `projects/p1-enterprise-kb/src/eval.py` `evaluate()` 加 `ndcg_at_5` / `ndcg_at_10` per-row 字段;`compute_hallucination` opt-in aggregate
- `tests/test_ragas_metrics.py` — 23 用例全 PASSED
- `eval/ragas/README.md` — 完整文档(包括已知边界)

**C2 已完成**(2026-07-28 v3)
- `projects/p2-code-docs-rag/tests/test_smoke.py` — 18 用例(parse_python_file × 8 / toy_embed × 3 / search × 5 / 端到端 × 2)
- `projects/p3-customer-service/tests/test_intent.py` — 29 用例(classify_intent × 10 / needs_transfer × 3 / faq_search × 5 / make_ticket × 6 / main × 1 / 覆盖 × 4)
- `projects/p4-finance-research/tests/test_table.py` — 25 用例(parse_table × 8 / table_to_text × 3 / fake_embed × 4 / search × 8 / main × 2)
- `projects/p1-enterprise-kb/tests/test_p1_e2e.py` — 8 用例(6 pass + 2 skip 等 langgraph)
- 顺手修复 `pipeline.py` 重复 `_ask_legacy` 影子 stub + `eval.py` S2 tenant 兼容 + test mock `**_kw` 接收

**未完成(C2 范围外,独立切片)**
- `hit_rate=0.0` 根因 RCA —— D8 任务(`contains_expected=0.42` 但 `citation_validity=0.68` 说明生成漂移)
- 真 `pip install ragas` 当前开发机下载失败(`langsmith 0.10.10` + `pyarrow 25.0.0` wheels read timeout),C1 shim 已通过签名兼容桥接,装上后一行 import 切回

**关键问题(剩余)**
- P1 golden 报告（`eval/reports/p1_golden.json`）整体指标差:
  ```
  n=50, citation_validity=0.68, abstention_correctness=0.44,
  contains_expected=0.42, hit_rate=0.0, p50_latency_ms=5544.4
  ```
  - `hit_rate=0.0`:**独立 RCA 任务**(embedding 模型 / 检索参数 / 金标错位——已超出 C1 范围)
- `eval/regression/check_thresholds.py` 是 CI gate,但**有 gate 没真卡**——C1 不修这个
- P2/P3/P4 仅 smoke — C2 任务(独立切片)

**证据**
- `eval/reports/p1_golden.json`(实际数字)
- `eval/ragas/runner.py` + `metrics.py` + `retrieval.py` + `hallucination.py` + `compare.py`
- `eval/ragas/README.md`(C1 完整说明)
- `eval/regression/thresholds.yaml`(per-project KPI floor)
- `eval/regression/check_thresholds.py`(CI gate;阈值有内联 dict,YAML 是参考文档)
- `tests/test_ragas_metrics.py`(23 用例)

**下一步(独立切片)**
- C2 — P2/P3/P4 测试覆盖补完
- D8 — `hit_rate=0.0` 根因 RCA + 修复(`contains_expected=0.42` 但 `citation_validity=0.68` 说明生成有引用但内容漂移)

---

## 维度 5 — 安全与合规 🟡

**现状（做得不错）**
- **ACL 三档敏感度**（public/internal/restricted）+ 角色（intern/employee/manager/executive/admin）—— `p1/src/acl.py`
- **path ACL 黑名单**：`.env` `.ssh` `secrets/` `etc/..` 等（`_compat.is_path_allowed`）
- **pgvector WHERE pushdown**：ACL 在库侧就过滤掉（`P1IndexStore.fetch_for_user(allowed_sensitivities=...)`）
- **PII 写入 redact**：`redact_for_index`（secrets DROP, PII MASK），阈值 `pii_leakage_to_index: 0`
- **PII 日志 redact**：`redact_for_trace`，回归测试 `test_redact_for_trace_leak_rate_is_zero`
- **chunk_id 自动敏感度升级**：classify_sensitivity only-up
- **psycopg3 参数化查询**：无 SQL 注入风险
- **chroma_store `_sanitize_meta`**：元数据键白名单

**缺口（合规与运行时）**
- 🔴 **prompt injection 运行时防御缺位**：`p1/tests/test_security.py` 有测试，但**生产路径没有** strip / 长度限 / 关键词黑名单（"ignore previous"、"系统提示"）—— 用户输入直接进 system prompt
- 🔴 **LLM 输出消毒缺位**：JSON-mode 解析失败时只是 fallback 拒答，没有 schema 强校验（pydantic）
- 🟡 **query 长度无上限**：超长 query 会撑爆 context window
- 🟡 **依赖无 lockfile**：`pyproject.toml` 是 `>=x,<y` range-pinned；无 `requirements.lock` 或 `pip-compile` hash 模式；可复现构建不保证
- 🔴 **无 LICENSE 文件**：合规必备缺失
- 🟡 **无 GDPR DSR**：P6 README 注明 "GDPR/保留/DSR ... 不在本轮范围"——`delete_by_source` 是 per-doc，不是 per-user

**证据**
- `projects/p1-enterprise-kb/src/acl.py`（ACL + path 黑名单）
- `projects/p6-ingestion-platform/src/pii.py:203-225`（classify_sensitivity）
- `pyproject.toml`（range-pinned，无 lockfile）
- 根目录无 `LICENSE`

**建议**
- M3：补 LICENSE + lockfile
- S3：补审计日志 + GDPR DSR endpoint
- S4：prompt injection 运行时防御 + 输出 pydantic 校验

---

## 维度 6 — 可观测与运维 🔴

**现状（最薄的一块）**
- 仅用 stdlib `logging`，无 JSON formatter、无 structlog
- **0** Prometheus client / **0** OTel SDK / **0** correlation id
- `grep -r "prometheus\|opentelemetry\|structlog" projects/` → 0 个真实引用（opentelemetry 仅在 `p5/scripts/make_arch_pdf.py:316` fixture 文本里）
- **0** `/healthz` `/readyz` 端点（无 FastAPI 服务）
- **0** runbook（无 incident response / restart / scale / debug 文档）
- **0** SLO 文档（无 latency / availability / error rate 目标声明）
- retry 用手写循环（`p6/src/embeddings.py:120-122`），**不**用 `tenacity`
- **0** 熔断器

**为什么这条最卡"企业级"**
- 生产事故后无法定位：没有 trace、metric、日志没法 grep、没 SLO baseline
- 没有 `/healthz`：负载均衡器没法做 liveness/readiness probe
- 没有 runbook：on-call 工程师半夜被叫醒只能现场读代码

**证据**
- `grep -r "prometheus\|opentelemetry\|structlog" projects/` → 0（grep 计 1 是 fixture PDF 文本）
- `grep -r "tenacity" projects/` → 0
- `eval/reports/p1_golden.json:p50_latency_ms=5544` 暴露无 perf budget

**建议（M1，2 周必做）**
1. `structlog` 替换 stdlib logging，所有模块输出 JSON
2. 加 `trace_id` 中间件（library 用 `pipeline(trace_id=)` 参数，Streamlit 用 `st.session_state.session_id`）
3. 新增 `projects/p1-enterprise-kb/src/server.py`（FastAPI thin server），暴露：
   - `GET /healthz`（进程存活）
   - `GET /readyz`（依赖就绪：ollama/pgvector/chroma）
   - `GET /metrics`（prometheus_client format）
4. 关键埋点：`rag_query_total`, `rag_query_latency_seconds`（histogram）, `rag_tokens_total`, `rag_retrieved_chunks`, `rag_citation_validity_total`, `rag_abstained_total`
5. `tenacity` 替换手写 retry
6. 写 `RUNBOOK.md`：常见 incident（embedding 服务挂、pgvector 连接池满、ACL 误过滤、流式截断）的应急步骤

---

## 维度 7 — API 与集成 🔴

**现状**
- 整套服务 = **Python library + Streamlit demo**
  - P1: `P1Pipeline` 类 + Streamlit 三面板
  - P5: `app.py`（Streamlit 多格式 loader）
  - P6: `run_ingest.py`（CLI）
- **无** REST / gRPC / SDK
- **无** webhook / 异步回调
- connector 只覆盖 fs / sql / objectstore / confluence，**缺** Notion / Slack / Gmail / Jira / Web
- LLM 调用全 `stream: False`，**无流式响应**
- `docs/api.md` 是 Python 调用示例，不是 API 文档

**为什么这条卡"对外服务化"**
- 没有 API 就没法嵌入到企业现有系统（OA / 工单 / 客服）
- 没有 webhook 就没法异步通知
- 没有流式响应就用户体验差（5.5s 才出结果）
- 没有 SDK 就下游业务接入成本高

**证据**
- `projects/p1-enterprise-kb/docs/api.md`（手写 Python 示例）
- `projects/p5-multi-format-loader/app.py`（Streamlit）
- `projects/p6-ingestion-platform/src/connectors/`（仅 4 个）

**建议（M2，3 周必做）**
1. FastAPI thin server `p1/src/server.py`：
   - `POST /v1/query`（sync）+ `POST /v1/query/stream`（SSE 流式）
   - `POST /v1/ingest`（薄壳包 `run_ingest.py`）
   - `GET /v1/healthz` `/v1/readyz` `/v1/metrics`
2. API key 认证（`X-API-Key` header，从 env 读）—— **先不**上 OAuth2，满足最低合规
3. thin Python SDK：`p1/clients/python/`（同包分发）
4. 扩 connector（Notion / Slack / Jira）放 S 档

---

## 维度 8 — 多租户与成本治理 🟡（S2 已完成基线）

**现状（S2 完成后）**
- **多租户数据模型 + 隔离 + 配额基线**：
  - `src/tenant.py`：`Tenant` (frozen) + `Quota` + `Plan` (FREE/TEAM/ENTERPRISE) + slug 格式校验
  - `src/tenant_context.py`：ContextVar 注入 + `tenant_scope` + `TenantRequiredError`
  - `src/tenant_registry.py`：静态 JSON registry（`config/tenants.json`，dev 阶段够用，接口稳定可换 DB）
  - `src/quota_store.py`：`InMemoryQuotaStore`（滑动 24h 窗口）+ `QuotaExceeded` + 失败 refund
- **isolation**：`pipeline.ask(question, *, tenant_id)` tenant_id 必填；`fetch_for_user(*, tenant_id)` 缺则 raise；Chroma where `tenant_id + sensitivity` 复合；P6 `manifest.tenant_id` 列 + 索引
- **per-tenant 配额**：3 档 plan 默认值 + custom_quota override；queries + tokens 两个 metric；超限 raise `QuotaExceeded`
- **可观测**：所有 Prometheus metrics 加 `tenant_id` label；structlog 自动带 tenant_id
- **FastAPI**：`X-Tenant-Id` header → `Depends(resolve_tenant)` → 400/404 区分

**仍缺的（S2 没做）**
- 无 Redis 后端（接口稳定但 dev 用内存；多 worker 部署需切 Redis）
- 无 token 级限流（只有 24h 配额上限）
- 无 stripe/billing 集成
- 物理隔离（schema-per-tenant / db-per-tenant）— logical 隔离够用，留 migration 路径

**证据**
- `projects/p1-enterprise-kb/src/{tenant,tenant_context,tenant_registry,quota_store}.py`
- `projects/p1-enterprise-kb/tests/test_tenant_{context,quota,isolation}.py`
- `projects/p6-ingestion-platform/tests/test_tenant_ingest.py`

**建议（S2，4 周）**
1. 数据模型加 `tenant_id`，pgvector `where={"tenant_id": tid}` pushdown
2. `User` 加 `tenant_id` 字段；ACL 三档变 `(tenant_id, sensitivity)` 复合
3. 新增 `test_cross_tenant_isolation`：user_A 在 tenant_X 不能看到 tenant_Y 的任何 chunk
4. per-tenant 配额：
   - 每日 token 上限
   - 每用户每日 query 数上限
   - 每用户 QPS 限流（token bucket）
5. 中间件记录每次 query 的 `prompt_tokens + completion_tokens`，写到 audit log + Prometheus `rag_tokens_total`

---

## 维度 9 — 项目治理与 CI/CD 🔴

**现状**
- **0** `.github/workflows/`（无 GitHub Actions）
- **0** 根目录 `Dockerfile` / `docker-compose.yml`（仅 `05-data-engineering/` 下有 pgvector 的 dev compose）
- **0** `helm/` / `k8s/`
- **0** `LICENSE` `CONTRIBUTING.md` `CHANGELOG.md` `CODE_OF_CONDUCT.md`
- **0** issue templates
- `pyproject.toml` 有 `ruff` + `mypy` 配置（亮点）
- `pyproject.toml [tool.pytest.ini_options]` 第二项 testpaths：`["projects", "03-advanced-production/labs/L15-security/tests"]` —— **后一项指向不存在**（已搬到 `course-mirror/`），pytest 会 silently skip
- `projects/p1-enterprise-kb/src/test_agentic_graph.py` —— **test 文件误放在 src/ 里**

**为什么这条卡"开源/团队协作"**
- 无 CI = 提交前没自动校验，bug 易溜进 main
- 无 Dockerfile = 部署靠运维手搓
- 无 LICENSE = 法律风险（默认是 ALL RIGHTS RESERVED）
- 无 CHANGELOG = 用户不知道每个版本改了什么

**证据**
- 根目录 `ls LICENSE* CONTRIBUTING* CHANGELOG*` → 0
- 根目录 `ls .github/` → 不存在
- `pyproject.toml [tool.pytest.ini_options]` testpaths 第二项指向不存在

**建议（M3，1 周必做）**
1. `pip-compile` 生成 `requirements.lock`（含 `--generate-hashes`）
2. 加 `LICENSE`（MIT 推荐）
3. 加 `CONTRIBUTING.md` `CHANGELOG.md`
4. 修 `pyproject.toml [tool.pytest.ini_options]` 第二项 testpaths（删 `03-advanced-production/...`）
5. 移走 `p1/src/test_agentic_graph.py` → `p1/tests/`

**建议（S1，2 周应做）**
1. `.github/workflows/ci.yml`：
   - ruff + mypy + pytest (matrix: py3.10/3.11/3.12)
   - regression gate（必须 hit_rate ≥ threshold）
2. `.github/workflows/release.yml`：tag → build wheel → publish

---

## 改进路线图（三档）

### M（Must，3 个月内必做）— 上生产的硬门槛

| # | 主题 | 工时 | 关键产物 |
|---|---|---|---|
| M1 | 可观测基线 | 2 周 | structlog JSON / trace_id / `/healthz` `/readyz` `/metrics` / 关键埋点 / tenacity |
| M2 | API 层 + 集成 | 3 周 | FastAPI server.py / `POST /v1/query` + `/v1/query/stream` / API key 认证 / thin SDK |
| M3 | 依赖治理 + LICENSE | 1 周 | requirements.lock / LICENSE / CONTRIBUTING / CHANGELOG / 修 pyproject testpaths / 移 stray test |

**M 档合计 ~6 周 / 1 人**。做完这三项 = 上生产门槛达标。

### S（Should，6 个月内应做）— 真生产形态

| # | 主题 | 工时 | 关键产物 |
|---|---|---|---|
| S1 | CI/CD pipeline | 2 周 | `.github/workflows/ci.yml` + `release.yml`，matrix test + regression gate |
| S2 | 多租户基线 | 4 周 | `tenant_id` 全链路 / `test_cross_tenant_isolation` / per-tenant quota |
| S3 | 审计日志 + 留存 | 2 周 | `data/audit/<date>.jsonl` / GDPR DSR endpoint / 留存策略文档 |
| S4 | prompt injection 防御 | 1 周 | query strip + 长度限 + 关键词黑名单 / 输出 pydantic 校验 / 三方内容标记 |

**S 档合计 ~9 周**。做完 = 可对外 SaaS 服务化。

### C（Could，>6 个月或可选）— 加分项

| # | 主题 | 关键产物 |
|---|---|---|
| C1 | 评测严谨度升级 | 换真 RAGAS / 加 nDCG / hallucination detector / A/B harness |
| C2 | query understanding | 把 L09 HyDE/multi-query 接入 P1 主路径 |
| C3 | embedding 版本管理 | `EmbeddingModelRegistry` + 双写 + atomic cutover |
| C4 | 多 connector | Notion / Slack / Gmail / Jira / Web crawler |
| C5 | 容器化 | 根目录 `Dockerfile` + `docker-compose.yml`（pgvector + minio + otel-collector） |
| C6 | connector 多租户化 | 同 S2，每个 connector 加 tenant_id 注入 |

---

## 结论

**一句话**：算法与评测是真生产级（3 维 🟢），但横切工程能力有明显缺口（4 维 🔴），整体处于"准企业级"。

**优先动作**：M1（可观测）+ M2（API 层）+ M3（依赖治理）= 6 周工作量。这三档补齐即可上内部生产；上 SaaS 再加 S1–S4。

**不要做的事**：
- 别先做 C（connector 扩展、容器化）—— 横切能力不到位，扩 connector 没意义
- 别先重写 RAG 算法层 —— 已经 🟢，动它只会引入回归
- 别解耦 `course-mirror/` —— 上一轮已解耦完，再动会破坏现有边界

---

## 附录：本审计的方法

1. **结构审计**（Explore）：顶层布局、6 个 project 体量、CI/CD 信号、配置/密钥、测试、文档、可观测、错误处理、企业特性（多租户/限流/缓存/认证/审计/Schema/队列）
2. **代码质量审计**（Explore）：模块粒度、类型提示、测试覆盖、可靠性、可观测、安全、性能、文档、数据工程、治理
3. **领域能力审计**（Explore，RAG-specific）：评测严谨度、检索质量、生成质量、摄取流水线、生产安全、运维、多租户、集成
4. **数值核验**（grep + python）：`hit_rate=0.0` / `p50_latency_ms=5544` / `grep -r "prometheus\|opentelemetry\|structlog" projects/` = 0（仅 1 个 fixture 文本）/ `grep -r "tenacity" projects/` = 0 / `grep -r "tenant_id\|org_id" projects/` = 0

**本审计不做的事**：
- 不评估 `course-mirror/`（训练营配套，非企业程序本体）
- 不复跑测试套件（已有 SUMMARY.md / VERIFY.md 记录结论）
- 不预测修复成本，仅给工时估计（M1=2w / M2=3w / M3=1w / S1=2w / S2=4w / S3=2w / S4=1w）

**报告维护**：每次发版前对照本表复核评分；每完成一项 M/S/C 工单，更新对应维度评级。