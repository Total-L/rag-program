# Audit Log & GDPR DSR（P1 — ENTERPRISE_AUDIT.md S3）

> **TL;DR**：每次 query 落 `data/audit/<YYYY-MM-DD>.jsonl`；`DELETE /v1/users/{user_id}/data` 抹除；留存 90 天可调；user_id 不存明文（HMAC 截断）。

---

## 1. 为什么需要审计日志

企业上线 KB 助手后，必须能回答这三类问题：

1. **合规**：某用户在 X 时间问了什么、得到了什么答案？（GDPR 第 15 条访问权、第 17 条被遗忘权）
2. **安全**：谁在探测企业知识？有没有 PII 被吞进 LLM？
3. **回溯**：金标回归失败时，能否找到那天到底问了什么、命中了哪些 chunk？

不写审计 = 三条全失分；事故后无法定位。

## 2. 数据模型与存储格式

### 2.1 文件布局

```
projects/p1-enterprise-kb/data/audit/
├── .gitkeep
├── 2026-07-25.jsonl   ← 每日一个文件
├── 2026-07-26.jsonl
└── 2026-07-27.jsonl
```

每个文件是 append-only **JSONL**（每行一个 JSON 对象）。为什么不存数据库：

- append 性能：JSONL 是 sequential write，DB 是 random write + transaction
- DSR 重写：一次 DELETE 只需重写匹配文件（同一天的多数 DSR 都在近期）
- 工具友好：`grep`/`awk`/`jq` 直接处理，不需要 SQL
- 90 天 retention：直接 `unlink`，不需要 vacuum

### 2.2 每行 schema

```json
{
  "ts": "2026-07-28T12:34:56+00:00",
  "trace_id": "a1b2c3d4e5f67890",
  "tenant_id": "acme-corp",
  "user_id_hash": "9f86d081884c7d65",
  "question_redacted": "员工年假怎么算？",
  "answer_redacted": "根据《员工手册》第 5 章...",
  "citations": ["E1", "E2"],
  "retrieved_source_ids": ["handbook-v3"],
  "retrieved_chunk_ids": ["abc123...", "def456..."],
  "abstained": false,
  "latency_ms": 542.3,
  "tokens_in": null,
  "tokens_out": null,
  "status": "success"
}
```

字段约束：

- **`user_id_hash`** = `HMAC-SHA256(salt, user_id)[:16]`。**永不存明文 user_id**。
  - salt 来自 env `AUDIT_USER_ID_SALT`；缺则启动期 warning（fallback dev salt）。
  - DSR 时按 hash 匹配；同一 salt 下 hash 一致即抹除。
- **`question_redacted`** + **`answer_redacted`** 已过 P6 `redact_for_trace`（9 类结构化 PII + 200/500 长度截断）。
- **`status`**：`success` / `abstained` / `error` / `not_ready`。注意 `abstained=true` 时仍是 success（正常拒答），`error` 是 pipeline 异常。
- **`tokens_in/out`**：MVP 暂未跟踪，固定 `null`。后续接 litellm 或 token 计数 SDK。

## 3. 配置（env）

| env | 默认值 | 含义 |
|---|---|---|
| `AUDIT_DIR` | `projects/p1-enterprise-kb/data/audit` | 落盘根目录；按 `YYYY-MM-DD.jsonl` 切文件 |
| `AUDIT_USER_ID_SALT` | `rag-program-dev-salt-DO-NOT-USE-IN-PROD` | HMAC salt；**生产必须设**（用 `python -c "import secrets; print(secrets.token_hex(32))"` 生成） |
| `AUDIT_RETENTION_DAYS` | `90` | 留存天数；启动期 lazy sweep 删除超期文件 |

代码层 `AuditLogger(**{base_dir, salt, retention_days})` 也可覆盖 env（测试用）。

## 4. GDPR DSR 流程

### 4.1 端点

```
DELETE /v1/users/{user_id}/data
Headers:
  X-API-Key: <server API key>          (与 /v1/query 一致)
  X-Tenant-Id: <tenant slug>           (强制；防跨租户删除)
Query:
  dry_run=true|false                   (默认 false)
```

### 4.2 行为

| 路径 | 行为 |
|---|---|
| 正常抹除 | 扫 `data/audit/*.jsonl` → 匹配 `user_id_hash == hmac(salt, user_id)[:16]` 的行 → 重写文件（原子 `os.replace`）→ 返回 `{removed_events: N, ...}` |
| `dry_run=true` | 同样的扫，但**不**写文件；返回 `{dry_run_removed_events: N, ...}`（预览用） |
| 重复调用 | 幂等；`removed_events: 0` |
| 找不到 user_id | `removed_events: 0`，HTTP 仍 200（避免泄漏 "用户存在" 信息） |

### 4.3 抹除范围 vs 不抹除范围

| 资产 | 抹除？ | 理由 |
|---|---|---|
| `data/audit/*.jsonl` 中 user_id_hash 匹配的行 | ✅ | GDPR 第 17 条 |
| Chroma / pgvector chunks | ❌ | P6 摄取的是公域文档，无 user_id 字段 |
| 其他租户的 audit | ❌ | 必须带 X-Tenant-Id，跨租户访问会被 `resolve_tenant` 阻断 |
| Prometheus metrics | ❌ | metrics 是聚合计数（Counter with labels），无法定位到单 user；合规上可接受 |
| structlog 日志（stdout） | ❌ | 日志中不写 user_id 明文（HMAC 截断）；如担心 stdout 留存可走 log shipper 端 DSR |

**为什么不抹除 Chroma chunk？**
GDPR 第 17 条只要求抹除"该数据主体的个人数据"。P6 摄取的 chunk 是公开/内部文档（如员工手册），里面**不存 user_id**，所以 chunk 本身不构成张三的个人数据。如果某条 chunk 里**确实**含张三的 PII（如报销单），那 PII redact 已经在索引前做过（详见 `src/pii.py`），所以向量里也已经是 `<PII_TYPE>` 标记，无明文。

### 4.4 DSR 本身的审计

DELETE 端点本身会在 structlog 写一行 `dsr_erase_user`：

```json
{
  "event": "dsr_erase_user",
  "tenant_id": "acme-corp",
  "user_id_hash": "9f86d081884c7d65",
  "removed_events": 7,
  "dry_run": false,
  "trace_id": "<请求 trace_id>",
  "ts": "..."
}
```

DSR 调用本身不进 audit log（避免循环依赖）；走日志即可。

## 5. 留存策略

### 5.1 默认 90 天

为什么 90 天：
- GDPR 未规定具体天数，但"不超过必要"是核心原则；
- 大多数企业合规审计周期 = 季度 + 30 天缓冲；
- 法务侧合同纠纷追诉期普遍 ≤ 1 年。

### 5.2 清理时机（lazy sweep）

```python
class AuditLogger:
    def __init__(self, ...):
        ...
        # 启动期 lazy sweep（不抛异常，最佳努力）
        try:
            self.retention_cleanup(days=self.retention_days)
        except Exception:
            pass
```

**为什么不做 cron**：
- MVP 阶段进程启动频繁（dev/test），lazy sweep 等价于每天跑一次；
- 真生产部署（24/7 long-lived process）应替换为独立 cron job（每天 04:00 跑），代码可复用 `AuditLogger.retention_cleanup()`。

### 5.3 修改留存天数

```bash
# 永久改：写进 .env 或 systemd EnvironmentFile
export AUDIT_RETENTION_DAYS=180

# 临时改：单次启动
AUDIT_RETENTION_DAYS=30 python -m server
```

缩短留存 = 立即生效（下次启动 lazy sweep 会删多余文件）。
延长留存 = 立即生效（不再删任何文件；旧文件自然恢复到新上限内）。

## 6. 失败处理（fail-loud but not blocking）

| 失败场景 | 行为 |
|---|---|
| `AUDIT_DIR` 不可写 | `AuditLogger.write()` 抛 `OSError` → pipeline 捕获 → `record_audit_write(status="failure")` 上报 + structlog `audit_write_failed` warning → **query 仍正常返回** |
| P6 `redact_for_trace` 不可用 | 降级为原文 + 长度截断（无 PII redact）→ log warning |
| `AUDIT_USER_ID_SALT` 缺失 | 启动期 warning，用 dev salt；query 正常 |
| 磁盘满 | OSError 同上；Prometheus 暴露 `rag_audit_write_total{status="failure"}` 持续增长 → 触发告警 |

**为什么写失败不阻断 query**：
GDPR 评估的核心是"已尽合理努力记录"；一次磁盘满永久阻塞业务 = 业务完全不可用 = 更大的事故。审计文件 + Prometheus 双通道失败信号让运维能定位并修复。

## 7. 测试覆盖

`tests/test_audit.py` 覆盖（12 用例）：

- `write_event_round_trip` — 单事件序列化 + 反序列化
- `write_appends_to_dated_file` — append 语义
- `write_cross_day_rotates_file` — 跨日切文件
- `write_redacts_pii_in_question_and_answer` — PII redact
- `write_failure_does_not_raise` — 不可写时不抛
- `hash_user_id_deterministic` — HMAC 一致
- `hash_user_id_different_salt_changes_value` — salt 改值改
- `hash_user_id_rejects_empty` — 拒绝空 user_id
- `dsr_erase_user_removes_matching_lines` — DSR 主路径
- `dsr_erase_user_dry_run_returns_count_without_writing` — dry-run
- `dsr_erase_user_idempotent` — 重复调用
- `retention_cleanup_removes_old_files` — 留存清理

## 8. 相关文件

| 文件 | 角色 |
|---|---|
| `src/audit.py` | AuditLogger + AuditEvent + hash_user_id + DSR + retention |
| `src/observability.py` | `record_audit_write()` metric 上报 |
| `src/pipeline.py` | `ask_agentic()` finally 块写 audit（失败不阻断） |
| `src/server.py` | `DELETE /v1/users/{user_id}/data` 端点 |
| `tests/test_audit.py` | 12 个单元测试 |
| `docs/audit-retention.md` | 本文档 |
| `ENTERPRISE_AUDIT.md` | 维度 5（安全合规）+ 维度 8（多租户治理）评分依据 |

## 9. 已知边界（不假装解决）

- **`tokens_in/out` 字段固定 `null`**：MVP 不跟踪 token 用量；接 litellm / 自实现 token counter 后再填。
- **stdout 日志留存**：structlog 输出到 stdout，由 log shipper 决定留存；本模块不控制。
- **多进程并发**：OS append 在 < 4KB 行上是原子写；真上 10k+ qps 时应考虑 `RotatingFileHandler` 或换 Kafka。
- **跨进程共享 audit 目录**：多 worker 部署时每个进程的 lazy retention sweep 会扫同一目录；删同一文件时可能 race。解决：retention 只在 worker-0 跑（lifespan 阶段做 leader election）。
- **审计文件本身备份**：本模块不做 S3/OSS 备份；备份由运维层负责（k8s PVC snapshot / cron rsync）。