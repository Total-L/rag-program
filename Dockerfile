# ─────────────────────────────────────────────────────────────
# rag_program — multi-stage Dockerfile（C5）
#
# 单镜像多 entrypoint：
#   api    → uvicorn P1 FastAPI server
#   ingest → P6 摄取 CLI
#   eval   → RAGAS 评测 CLI
#
# 跑法：
#   docker build -t rag-program:dev .
#   docker run --rm -p 8080:8080 -e RAG_PROG_API_KEY=secret rag-program:dev api
#   docker run --rm -v $(pwd)/data:/app/data rag-program:dev ingest --tenant acme-corp
#
# 设计取舍（per CLAUDE.md "Simplicity First"）：
#   - Python 3.11-slim（~150MB 基础镜像；alpine 太多 wheel 兼容问题）
#   - builder 阶段装 deps（含 [observability] / [course-mirror]），runtime 仅 copy
#   - 非 root user 跑（uid 10001；防容器逃逸到 host）
#   - 不写多镜像 / per-service Dockerfile — 同一份代码不同 entrypoint 足够 MVP
#   - HEALTHCHECK 用 /v1/healthz（依赖 API server 启动才能 ping）
# ─────────────────────────────────────────────────────────────

# ── Stage 1: builder ─────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# 装构建依赖（仅 builder 阶段要；runtime 不需要 gcc）
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# 先 copy 仅依赖文件 → 缓存命中（源码变更不重装 pip）
COPY pyproject.toml README.md ./
COPY projects/ ./projects/
COPY datasets/ ./datasets/
COPY eval/ ./eval/
COPY examples/ ./examples/

# 装主依赖（含 course-mirror + observability 可选 extras）
# `pip install --no-cache-dir` 减镜像体积
# `--extra-index-url` 不用：本项目无私有包
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir ".[course-mirror,observability]" 2>&1 | tail -5 || \
    pip install --no-cache-dir ".[observability]"


# ── Stage 2: runtime ─────────────────────────────
FROM python:3.11-slim AS runtime

# 安装运行时系统依赖（curl 给 HEALTHCHECK；tini 给 PID 1 信号处理）
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 rag && useradd -u 10001 -g rag -m -s /bin/bash raguser

WORKDIR /app

# 从 builder 复制 site-packages + 我们的源码
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app/projects /app/projects
COPY --from=builder /app/datasets /app/datasets
COPY --from=builder /app/eval /app/eval
COPY --from=builder /app/examples /app/examples
COPY --from=builder /app/pyproject.toml /app/README.md /app/

# 数据 / 审计目录（容器内非持久化；mount volume 覆盖）
RUN mkdir -p /app/data/audit /app/data/index && \
    chown -R raguser:rag /app

USER raguser

# 环境变量（运行时按需覆盖）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    RAG_PROG_API_PORT=8080 \
    AUDIT_DIR=/app/data/audit \
    O6_PG_DSN="" \
    OTEL_ENABLED=false

EXPOSE 8080

# tini 作 PID 1（处理 SIGTERM / SIGINT，优雅关闭 uvicorn）
ENTRYPOINT ["/usr/bin/tini", "--"]

# 默认进 api（开发期最常用）；docker run ... ingest 覆盖
CMD ["api"]

# ── 多 entrypoint（用 shell 脚本分发）──
# 用 script 而不是 Python 入口，便于容器日志清晰
COPY --chown=raguser:rag --chmod=755 <<'EOF' /usr/local/bin/rag-entrypoint.sh
#!/bin/bash
# rag-entrypoint.sh — 按第一个参数分发到不同服务
set -e
case "${1:-api}" in
  api)
    echo "→ starting P1 FastAPI server on :${RAG_PROG_API_PORT:-8080}"
    exec uvicorn projects.p1-enterprise-kb.src.server:app \
      --host 0.0.0.0 --port "${RAG_PROG_API_PORT:-8080}" \
      --workers "${RAG_PROG_UVICORN_WORKERS:-1}"
    ;;
  ingest)
    echo "→ running P6 ingest CLI"
    shift
    exec python -m src.run_ingest "$@"
    ;;
  eval)
    echo "→ running RAGAS eval"
    shift
    exec python -m eval.ragas.run "$@"
    ;;
  shell)
    echo "→ interactive shell (raguser)"
    exec bash
    ;;
  *)
    echo "Unknown entrypoint: $1"
    echo "Usage: rag-entrypoint.sh {api|ingest|eval|shell}"
    exit 1
    ;;
esac
EOF

# 重新覆盖 ENTRYPOINT 用我们的 shell script
ENTRYPOINT ["/usr/local/bin/rag-entrypoint.sh"]

# Liveness：P1 server 启动后 GET /v1/healthz 返回 200
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:${RAG_PROG_API_PORT:-8080}/v1/healthz || exit 1