# K8S 真部署指南（ENTERPRISE_AUDIT S5）

本文档说明如何把 `rag-program` RAG API 部署到生产 Kubernetes 集群。

适用版本：

| 组件 | 版本要求 |
|---|---|
| Kubernetes | ≥ 1.27（autoscaling/v2、policy/v1、networking.k8s.io/v1）|
| Helm | ≥ 3.13 |
| 镜像 | `ghcr.io/totallai/rag-program:0.1.0`（CI release 自动推）|
| 存储 | RWX 或 RWO PVC；audit 与 data 各一份 |

## 1. 前置

### 1.1 镜像

CI 在每次 `v*.*.*` tag 推 `release.yml`，用 OIDC trusted publishing 推：

- **GitHub Container Registry**：`ghcr.io/<owner>/rag-program:<tag>`
- **PyPI**：`pip install rag-program==<version>`

镜像 build：`Dockerfile`（multi-stage, non-root uid 10001, tini PID 1, HEALTHCHECK curl /v1/healthz）。

### 1.2 必备 secret

部署前必须生成：

```bash
# 32 字节 hex
openssl rand -hex 32   # RAG_PROG_API_KEY
openssl rand -hex 32   # AUDIT_USER_ID_SALT

# mmx / llm 后端 key
echo $ANTHROPIC_API_KEY
```

**不要**把这些写在 git。生产用 Sealed Secrets / External Secrets / Vault 任一方案接入。

### 1.3 集群要求

- 至少 1 个 storage class（默认或 gp3 / nfs-csi）
- 至少 1 个 ingress controller（nginx 推荐）
- metrics-server（HPA 需要）

## 2. 部署步骤

### 2.1 最小部署（开发 / staging）

```bash
helm install rag ./deploy/helm/rag-program \
  --namespace rag --create-namespace \
  --set config.RAG_PROG_API_KEY="$(openssl rand -hex 32)" \
  --set secrets.AUDIT_USER_ID_SALT="$(openssl rand -hex 32)" \
  --set secrets.ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"
```

### 2.2 生产部署

写一份 `values-prod.yaml`：

```yaml
replicaCount: 3
ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/proxy-body-size: "10m"
  hosts:
    - host: rag.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - hosts: [rag.example.com]
      secretName: rag-tls
autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
podDisruptionBudget:
  enabled: true
  minAvailable: 2
persistence:
  storageClass: gp3
  auditSize: 50Gi      # 90 天 audit log 预算
  dataSize: 100Gi
otel:
  enabled: true
  endpoint: http://otel-collector.observability.svc:4317
secrets:
  # base64 编码；这里用占位,真实部署走 sealed-secrets
  ANTHROPIC_API_KEY: <base64>
  AUDIT_USER_ID_SALT: <base64>
  POSTGRES_PASSWORD: <base64>
config:
  RAG_PROG_DEFAULT_TENANT: acme
  LOG_LEVEL: INFO
```

```bash
kubectl create namespace rag
helm install rag ./deploy/helm/rag-program \
  -n rag \
  -f values-prod.yaml
```

### 2.3 验证

```bash
# 1. pods 跑起来
kubectl -n rag get pods -l app.kubernetes.io/name=rag-program

# 2. readiness 探针通过(20s 内)
kubectl -n rag wait --for=condition=ready \
  pod -l app.kubernetes.io/name=rag-program --timeout=120s

# 3. port-forward + curl
kubectl -n rag port-forward svc/rag-rag-program 8080:80
curl -s -H "X-API-Key: $KEY" -H "X-Tenant-Id: acme" \
  http://localhost:8080/v1/readyz | jq

# 4. 实际 query
curl -s -X POST -H "X-API-Key: $KEY" -H "X-Tenant-Id: acme" \
  -H "Content-Type: application/json" \
  -d '{"question":"员工年假几天？","user_id":"alice","user_role":"executive"}' \
  http://localhost:8080/v1/query | jq
```

## 3. 持久化

| PVC | 用途 | 大小建议 |
|---|---|---|
| `audit` | `data/audit/<YYYY-MM-DD>.jsonl` 日志（S3 合规要求 90 天）| 50 Gi / 90 天（按 100 QPS 估算）|
| `data` | Chroma 向量库 + 上传缓存 + BM25 cache | 100 Gi+ |

PVC 默认保留（`helm uninstall` 不删）。手动清理：

```bash
kubectl -n rag delete pvc -l app.kubernetes.io/instance=rag
```

## 4. 配置项（ConfigMap）

通过 `--set config.KEY=VALUE` 注入。常用：

| Key | 默认 | 说明 |
|---|---|---|
| `RAG_PROG_API_KEY` | （空 → 启动 fail loud）| X-API-Key 头校验 |
| `RAG_PROG_LLM_BACKEND` | `mmx` | mmx / ollama |
| `RAG_PROG_AUDIT_DIR` | `/var/lib/rag-program/audit` | 写入 audit PVC |
| `RAG_PROG_DATA_DIR` | `/var/lib/rag-program/data` | 写入 data PVC |
| `RAG_PROG_MAX_QUERY_LEN` | `2000` | query 字符上限（fail-closed）|
| `RAG_PROG_GUARD_FAIL_CLOSED` | `true` | prompt injection fail-closed 开关 |
| `RAG_PROG_DEFAULT_TENANT` | `default` | 缺 X-Tenant-Id 兜底 |
| `LOG_LEVEL` | `INFO` | structlog 级别 |
| `OTEL_ENABLED` | `false` | 启用 OTLP exporter 需 otel.enabled=true |

敏感项走 `secrets` 段 + Secret：API key / DB password / audit salt。

## 5. 安全清单（部署前必查）

- [ ] Secret 用 Sealed Secrets / External Secrets 注入（**不要** git 提交 base64）
- [ ] `AUDIT_USER_ID_SALT` 至少 32 字节且每次部署唯一
- [ ] `RAG_PROG_API_KEY` 至少 32 字节
- [ ] 集群启用 PodSecurity 准入（至少 baseline,推荐 restricted）
- [ ] NetworkPolicy 限制 pod 只允许 ingress controller 访问 8080
- [ ] TLS 由 cert-manager 自动签（ingress annotations）
- [ ] audit PVC 加密（storage class 支持 encryption）

## 6. 升级 / 回滚

```bash
# 升级
helm upgrade rag ./deploy/helm/rag-program -n rag -f values-prod.yaml

# 回滚
helm history rag -n rag
helm rollback rag 3 -n rag
```

ConfigMap / Secret 变化会自动触发 deployment rollout
（pod annotation `checksum/config` + `checksum/secret`）。

镜像 tag 升级：

```bash
helm upgrade rag ./deploy/helm/rag-program \
  -n rag -f values-prod.yaml \
  --set image.tag=0.1.1
```

## 7. 备份 / 恢复

### audit 日志

```bash
# 备份(每日 cron 建议跑)
kubectl -n rag exec -it $(kubectl -n rag get pod -l app.kubernetes.io/name=rag-program -o name | head -1) -- \
  tar czf /tmp/audit-$(date +%Y%m%d).tar.gz /var/lib/rag-program/audit

# 拷到 S3
kubectl -n rag cp <pod>:/tmp/audit-20260101.tar.gz ./audit-20260101.tar.gz
aws s3 cp ./audit-20260101.tar.gz s3://rag-backups/audit/2026/
```

### Chroma 向量库

rebuild from source 是更可靠的恢复方式：

```bash
kubectl -n rag exec -it <pod> -- python -m src.run_ingest \
  --tenant acme --source /data/source
```

## 8. 监控

Prometheus 通过 pod annotations 自动抓 `/metrics`：

```yaml
prometheus.io/scrape: "true"
prometheus.io/port: "8080"
prometheus.io/path: "/metrics"
```

关键 SLO（详见 `docs/SLO.md`）：

| 指标 | 目标 |
|---|---|
| P50 latency | < 1.5s |
| P95 latency | < 5s |
| 可用性 | 99.5% |
| 错误率 | < 1% |
| 引用有效率 | > 95% |

PromQL example：

```promql
# P95 latency by tenant
histogram_quantile(0.95,
  sum by (tenant_id, le) (rate(rag_query_latency_seconds_bucket[5m]))
)

# 5xx / total
sum(rate(rag_queries_total{status="internal_error"}[5m]))
/
sum(rate(rag_queries_total[5m]))
```

## 9. OpenTelemetry（可选）

`otel.enabled=true` + 配置 endpoint 后自动启用 OTLP exporter。

支持的 SDK：`opentelemetry-api/sdk/exporter-otlp/instrumentation-fastapi`
（lockfile 在 `requirements-otel.lock`，独立栈）。

镜像内默认不装 OTel（避免 prod 多 80MB）。Helm chart 不强制。

## 10. 故障排查（incidents）

| 症状 | 检查项 | 修复 |
|---|---|---|
| CrashLoopBackOff | `kubectl describe` 看事件 | 多半是 ConfigMap/Secret 缺值 → 起 fail loud |
| readiness 探针 fail | `curl /v1/readyz` | 看 Chroma/pgvector/embedding backend 是否健康 |
| p95 暴涨 | `kubectl top pod` + Prom metrics | Chroma 池满 → 加 pod；embed 后端挂 → 切 fallback |
| audit 不写 | `df -h /var/lib/rag-program/audit` | PVC 满 → 扩 auditSize 或加留存清理 |
| 跨租户查到别人数据 | 检查 tenant_id 流转 | `kubectl logs \| grep tenant_id`；P1 已知 bug 见 RCA |

详见 `docs/RUNBOOK.md`。

## 11. 卸载

```bash
helm uninstall rag -n rag
kubectl delete namespace rag  # 同时删 PVC / ConfigMap / Secret
```

`helm uninstall` 单独跑会保留 PVC 与 namespace。

## 12. CI 集成

`.github/workflows/release.yml` 在 tag 触发时：

1. 跑 gate（lint + test + drift check）
2. build wheel + sdist
3. publish TestPyPI（workflow_dispatch）或 PyPI（tag 推）
4. 创建 GitHub Release + 上传 artifact

镜像自动 push 到 `ghcr.io`：需在 GitHub repo settings → Packages → Configure access scopes 勾选 workflow 默认权限。

## 13. Helm chart 版本

- chart version 0.1.0（与 app version 对齐）
- 后续 schema 变更递增 major
- 详见 `deploy/helm/rag-program/Chart.yaml`