# 容器化部署（rag_program — ENTERPRISE_AUDIT.md C5）

> **TL;DR**：`docker compose up -d` 一键起 P1 API + Postgres/pgvector + Ollama + MinIO + OTel + Prometheus + Grafana。生产部署见 §5。

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                     Docker Compose Stack                          │
│                                                                   │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐       │
│  │   api   │───►│postgres │    │ ollama  │    │  minio  │       │
│  │ P1 API  │    │pgvector │    │embed+LLM│    │   S3    │       │
│  │ :8080   │    │  :5432  │    │ :11434  │    │ :9000   │       │
│  └────┬────┘    └─────────┘    └─────────┘    └─────────┘       │
│       │                                                         │
│       │ OTLP                                                     │
│       ▼                                                         │
│  ┌─────────────┐    ┌─────────┐    ┌─────────┐                  │
│  │otel-collector│───►│prometheus│───►│ grafana │                  │
│  │   :4317     │    │  :9090  │    │  :3000  │                  │
│  └─────────────┘    └─────────┘    └─────────┘                  │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

## 2. 快速起步（dev）

### 2.1 前置条件

- Docker 24+ / Docker Compose v2
- 8GB RAM（Ollama 模型加载用）
- (可选) NVIDIA GPU + nvidia-docker（embedding / LLM 加速）

### 2.2 一键起

```bash
# 1. 复制 env 模板
cp .env.example .env
# 2. 编辑 .env：改 AUDIT_USER_ID_SALT / RAG_PROG_API_KEY（dev 可留空）
# 3. 起 stack
docker compose up -d --build
# 4. 等健康检查通过（约 30s）
docker compose ps
# 5. 验证
curl http://localhost:8080/v1/healthz   # → {"status":"ok"}
curl http://localhost:9090/-/healthy    # Prometheus
open http://localhost:3000              # Grafana（admin/admin）
```

### 2.3 跑一次摄取

```bash
# 容器内跑 P6 ingest CLI
docker compose run --rm api ingest --tenant acme-corp

# 或者 attach 到已运行容器
docker compose exec api bash
> ingest --tenant acme-corp
```

### 2.4 跑评测

```bash
docker compose run --rm api eval --project p1-enterprise-kb
```

### 2.5 停 / 重建 / 清理

```bash
docker compose down              # 停 + 删容器（保留卷）
docker compose down -v           # 删容器 + 删卷（**会清数据！**）
docker compose build api --no-cache   # 重建 api 镜像
docker compose logs -f api       # 看 api 日志
```

## 3. 镜像分层

`Dockerfile` 用多阶段构建：

| Stage | 作用 | 镜像大小 |
|---|---|---|
| `builder` | 装 build-essential + pip install 含 `[observability]` extras | ~1.5GB（不进最终镜像）|
| `runtime` | python:3.11-slim + curl + tini + copy site-packages | ~600MB（最终镜像）|

**单镜像多 entrypoint**（按 `docker run ... <entrypoint>` 覆盖）：
- `api`（默认）→ uvicorn P1 FastAPI server
- `ingest` → P6 摄取 CLI（`python -m src.run_ingest`）
- `eval` → RAGAS 评测（`python -m eval.ragas.run`）
- `shell` → bash 调试用

## 4. 服务间依赖

| 服务 | 端口 | 健康检查 | 依赖 |
|---|---|---|---|
| api | 8080 | `curl /v1/healthz` | postgres, ollama, otel-collector |
| postgres | 5432 | `pg_isready` | — |
| ollama | 11434 | （无；首次拉模型慢）| — |
| minio | 9000/9001 | `curl /minio/health/live` | — |
| otel-collector | 4317/4318/8889 | （内置） | prometheus |
| prometheus | 9090 | （内置） | — |
| grafana | 3000 | （内置） | prometheus |

`depends_on` 用 `condition: service_healthy` 避免冷启动 race。

## 5. 部署到生产（与 dev 的差异）

| 项 | dev | staging | 生产 |
|---|---|---|---|
| RAG_PROG_API_KEY | 空 | 32 字符随机 | Vault / KMS 注入 |
| AUDIT_USER_ID_SALT | dev fallback | 16B 随机 | 32B 随机；定期轮换 |
| Postgres 密码 | `rag`/`rag` | 16 字符随机 | RDS / Cloud SQL + IAM |
| MinIO | docker container | 同 dev | S3 / OSS（外部） |
| OTLP endpoint | otel-collector | 同 dev | 远端 Jaeger / Tempo / Honeycomb |
| Grafana admin | admin/admin | 改 | SSO / OAuth |
| Ollama | 容器内 | 同 dev / 拆出 | 独立 GPU 节点 / 远端 API |
| 镜像 tag | `dev` | git SHA | git SHA + signature verify |

**生产部署不直接用本 `docker-compose.yml`**——本文件只覆盖 dev / staging。生产应走 K8s helm chart 或 Cloud Run / ECS。

## 6. 常见 trap

### 6.1 Ollama 模型未预拉

**症状**：`/v1/query` 第一次调用延迟 30s+（冷加载模型）。
**修**：
```bash
docker compose exec ollama ollama pull llama3.2:1b
docker compose exec ollama ollama pull nomic-embed-text
```

### 6.2 pgvector 扩展未装

**症状**：连 Postgres 后 `CREATE EXTENSION vector` 失败。
**说明**：本 compose 用 `pgvector/pgvector:pg16` 镜像，扩展**已内置**。若是换镜像需手动 `CREATE EXTENSION IF NOT EXISTS vector;`

### 6.3 端口冲突

**症状**：`bind: address already in use`。
**修**：改 `docker-compose.yml` 端口映射（如 `"8081:8080"`）；或停占 8080/5432/9090/3000/9000/9001/11434 的进程。

### 6.4 AUDIT_DIR 持久化

**症状**：容器重启后 audit log 丢失。
**说明**：compose 已把 `rag_data:/app/data` mount 到命名卷；若要 host 持久化改 `./data:/app/data`。

### 6.5 OTel 包没装

**症状**：`OTEL_ENABLED=true` 时 api 启动 warning `otel_disabled_missing_packages`。
**修**：
```bash
docker compose build api --build-arg INSTALL_EXTRAS=observability
```
或修改 Dockerfile 把 `pip install ".[course-mirror,observability]"` 改成强制装。

## 7. 安全检查清单

部署到 staging 之前确认：

- [ ] `RAG_PROG_API_KEY` ≥ 32 字符随机
- [ ] `AUDIT_USER_ID_SALT` ≥ 16 字节随机且与 dev 不同
- [ ] Postgres 密码改强（`rag`/`rag` 仅 dev）
- [ ] MinIO 改密码（`minioadmin`/`minioadmin` 仅 dev）
- [ ] Grafana 改密码（`admin`/`admin` 仅 dev）
- [ ] `/v1/metrics` 与 `/v1/healthz` 不对外暴露（K8s 用 ClusterIP / 内网）
- [ ] `OTLP` endpoint 走 TLS（生产）

## 8. 相关文件

| 文件 | 角色 |
|---|---|
| `Dockerfile` | 多阶段构建；单镜像多 entrypoint |
| `docker-compose.yml` | 7 服务编排 |
| `.dockerignore` | 排除 venv / data / course-mirror |
| `infra/otel-collector-config.yaml` | OTLP receivers / Prometheus exporter |
| `infra/prometheus.yml` | scrape config（api + otel-collector）|
| `.env.example` | 所有可调 env 变量模板 |
| `docs/CONTAINER.md` | 本文档 |
| `ENTERPRISE_AUDIT.md` | 维度 9（CI/CD）+ C5 备注 |

## 9. 已知边界（不假装解决）

- **K8s manifests**：未做（helm/kustomize 留给生产运维）
- **CI publish to registry**：未做（GitHub Actions publish workflow 留 C 档后续）
- **多镜像拆分**：单镜像多 entrypoint；真上微服务应拆 4-5 个 Dockerfile
- **预拉 Ollama 模型**：compose 没自动跑 `ollama pull`（避免冷启动卡 build）；手动 §6.1
- **Prometheus 远端存储**：本地 TSDB 30 天；生产应接 Thanos / Cortex
- **Grafana dashboards**：内置 Prometheus data source 但**未自动 provisioning dashboards**；手动从 Grafana UI 配 / 用 json file provisioning（留 TODO）

## 10. 验证清单

部署完跑：

```bash
# 1. 健康检查
curl -fsS http://localhost:8080/v1/healthz
# 2. readiness（api 启动 + 已索引）
curl -fsS http://localhost:8080/v1/readyz
# 3. metrics
curl -fsS http://localhost:8080/v1/metrics | head -5
# 4. 端到端 query（带 tenant header）
curl -fsS -X POST http://localhost:8080/v1/query \
  -H "X-Tenant-Id: acme-corp" \
  -H "Content-Type: application/json" \
  -d '{"question":"测试问题"}'
# 5. DSR endpoint
curl -fsS -X DELETE http://localhost:8080/v1/users/test-user/data \
  -H "X-Tenant-Id: acme-corp"
# 6. Prometheus targets up
curl -fsS http://localhost:9090/api/v1/targets | jq '.data.activeTargets[].labels.job'
```