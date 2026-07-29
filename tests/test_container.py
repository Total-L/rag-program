"""容器化配置语法测试（ENTERPRISE_AUDIT C5）。

覆盖：
1. Dockerfile / docker-compose.yml / infra configs / .env.example 存在
2. YAML 语法解析（不实际启动 docker）
3. docker-compose 包含关键服务 + 端口 + 健康检查
4. .env.example 包含必需变量

不跑 `docker build` / `docker compose up`（CI 没 docker daemon），
仅做静态语法验证 — 避免 docker-in-docker 复杂度。

跑：
    pytest tests/test_container.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ── fixtures ──


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    return (ROOT / "Dockerfile").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dockerignore_text() -> str:
    return (ROOT / ".dockerignore").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose_data() -> dict:
    """解析 docker-compose.yml — 用 PyYAML。"""
    import yaml

    with (ROOT / "docker-compose.yml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def otel_config() -> dict:
    """解析 OTel collector 配置。"""
    import yaml

    with (ROOT / "infra" / "otel-collector-config.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def prometheus_config() -> dict:
    """解析 Prometheus 配置。"""
    import yaml

    with (ROOT / "infra" / "prometheus.yml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def env_example_text() -> str:
    return (ROOT / ".env.example").read_text(encoding="utf-8")


# ── 1. 文件存在 ──


def test_dockerfile_exists():
    """Dockerfile 存在 + 大小合理（不全空）。"""
    p = ROOT / "Dockerfile"
    assert p.exists(), "Dockerfile 必须存在"
    assert p.stat().st_size > 500, "Dockerfile 不应小于 500 字节"


def test_dockerignore_exists():
    p = ROOT / ".dockerignore"
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert ".venv/" in content
    assert "data/" in content
    assert ".git/" in content


def test_docker_compose_yml_exists():
    p = ROOT / "docker-compose.yml"
    assert p.exists()


def test_infra_configs_exist():
    assert (ROOT / "infra" / "otel-collector-config.yaml").exists()
    assert (ROOT / "infra" / "prometheus.yml").exists()


def test_env_example_exists():
    assert (ROOT / ".env.example").exists()


def test_container_doc_exists():
    assert (ROOT / "docs" / "CONTAINER.md").exists()


# ── 2. Dockerfile 关键指令 ──


def test_dockerfile_has_multistage_build(dockerfile_text):
    """Dockerfile 用多阶段构建（builder + runtime）。"""
    assert "FROM python:3.11-slim AS builder" in dockerfile_text
    assert "FROM python:3.11-slim AS runtime" in dockerfile_text


def test_dockerfile_uses_non_root_user(dockerfile_text):
    """运行时用非 root user（防容器逃逸）。"""
    assert "useradd" in dockerfile_text or "USER" in dockerfile_text
    assert "USER raguser" in dockerfile_text


def test_dockerfile_has_healthcheck(dockerfile_text):
    assert "HEALTHCHECK" in dockerfile_text
    assert "/v1/healthz" in dockerfile_text


def test_dockerfile_has_tini_init(dockerfile_text):
    """tini 作 PID 1（处理 SIGTERM）。"""
    assert "tini" in dockerfile_text


def test_dockerfile_has_multiple_entrypoints(dockerfile_text):
    """单镜像多 entrypoint（api / ingest / eval / shell）。"""
    for ep in ("api", "ingest", "eval", "shell"):
        assert f'{ep})' in dockerfile_text or f'"{ep}"' in dockerfile_text


# ── 3. docker-compose.yml 关键服务 ──


def test_compose_required_services_present(compose_data):
    """7 个核心服务都存在。"""
    services = compose_data["services"]
    required = {"api", "postgres", "ollama", "minio", "otel-collector", "prometheus", "grafana"}
    missing = required - services.keys()
    assert not missing, f"missing services: {missing}"


def test_compose_api_exposes_8080(compose_data):
    """api 服务暴露 8080。"""
    ports = compose_data["services"]["api"]["ports"]
    # ports 可能是 "8080:8080" 或 list of dict
    assert any("8080:8080" in str(p) for p in ports)


def test_compose_api_has_healthcheck(compose_data):
    """api 服务有健康检查（curl /v1/healthz）。"""
    hc = compose_data["services"]["api"].get("healthcheck", {})
    test = hc.get("test", [])
    assert any("healthz" in str(t) for t in test), f"api healthcheck 应检查 /v1/healthz，实际：{test}"


def test_compose_api_depends_on_postgres(compose_data):
    """api 强依赖 postgres（启动顺序）。"""
    deps = compose_data["services"]["api"].get("depends_on", {})
    if isinstance(deps, dict):
        assert "postgres" in deps
        # 用 service_healthy 而非 service_started
        assert deps["postgres"].get("condition") == "service_healthy"
    else:
        # 简写 list 形式
        assert "postgres" in deps


def test_compose_postgres_uses_pgvector_image(compose_data):
    """postgres 用 pgvector 镜像（不是普通 postgres）。"""
    image = compose_data["services"]["postgres"]["image"]
    assert "pgvector" in image, f"必须用 pgvector 镜像，实际：{image}"


def test_compose_otel_collector_otlp_endpoint(compose_data):
    """otel-collector 暴露 OTLP gRPC (4317) + HTTP (4318)。"""
    ports = compose_data["services"]["otel-collector"]["ports"]
    ports_str = " ".join(str(p) for p in ports)
    assert "4317" in ports_str, "OTLP gRPC 必须暴露 4317"
    assert "4318" in ports_str, "OTLP HTTP 必须暴露 4318"


def test_compose_prometheus_scrapes_api(compose_data):
    """prometheus 服务的 command 引用 prometheus.yml。"""
    cmd = compose_data["services"]["prometheus"].get("command", [])
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    assert "prometheus.yml" in cmd_str


def test_compose_has_persistent_volumes(compose_data):
    """关键数据走命名 volume（postgres / ollama / minio / prometheus / grafana）。"""
    volumes = compose_data.get("volumes", {})
    required_vols = {"postgres_data", "ollama_data", "minio_data", "prometheus_data", "grafana_data"}
    missing = required_vols - volumes.keys()
    assert not missing, f"missing volumes: {missing}"


def test_compose_has_internal_network(compose_data):
    """自定义 bridge network（服务间隔离 + DNS 互通）。"""
    networks = compose_data.get("networks", {})
    assert "ragnet" in networks, "应有名为 ragnet 的 bridge network"


# ── 4. OTel 配置 ──


def test_otel_has_otlp_receiver(otel_config):
    """OTel collector 配置含 OTLP receiver。"""
    receivers = otel_config.get("receivers", {})
    assert "otlp" in receivers, "必须配 OTLP receiver"
    otlp = receivers["otlp"]
    protocols = otlp.get("protocols", {})
    assert "grpc" in protocols, "OTLP gRPC 必须开"
    assert "http" in protocols, "OTLP HTTP 必须开"


def test_otel_has_prometheus_exporter(otel_config):
    """OTel collector 配置含 Prometheus exporter（给 Grafana）。"""
    exporters = otel_config.get("exporters", {})
    assert "prometheus" in exporters, "必须配 Prometheus exporter"
    assert exporters["prometheus"].get("endpoint", "").endswith(":8889")


def test_otel_metrics_pipeline_runs(otel_config):
    """metrics pipeline: receivers → processors → exporters 全配齐。"""
    pipelines = otel_config.get("service", {}).get("pipelines", {})
    metrics_pipeline = pipelines.get("metrics", {})
    assert metrics_pipeline, "必须配 metrics pipeline"
    assert "otlp" in metrics_pipeline.get("receivers", [])
    assert "prometheus" in metrics_pipeline.get("exporters", [])


# ── 5. Prometheus 配置 ──


def test_prometheus_scrapes_api(prometheus_config):
    """Prometheus 抓取 rag-api 服务。"""
    scrape_jobs = prometheus_config.get("scrape_configs", [])
    api_jobs = [j for j in scrape_jobs if j.get("job_name") == "rag-api"]
    assert api_jobs, "必须有 rag-api scrape job"
    targets = api_jobs[0]["static_configs"][0]["targets"]
    assert "api:8080" in targets


def test_prometheus_scrapes_otel_collector(prometheus_config):
    """Prometheus 抓取 otel-collector。"""
    scrape_jobs = prometheus_config.get("scrape_configs", [])
    otel_jobs = [j for j in scrape_jobs if j.get("job_name") == "otel-collector"]
    assert otel_jobs, "必须有 otel-collector scrape job"


def test_prometheus_has_global_interval(prometheus_config):
    global_cfg = prometheus_config.get("global", {})
    assert "scrape_interval" in global_cfg


# ── 6. .env.example ──


def test_env_example_has_required_vars(env_example_text):
    """`.env.example` 包含 C5 必需变量。"""
    required = {
        "RAG_PROG_API_KEY",
        "AUDIT_USER_ID_SALT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "P1_PG_DSN",
        "O6_PG_DSN",
        "S3_ENDPOINT",
        "S3_ACCESS_KEY",
        "OTEL_ENABLED",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
    }
    missing = required - set(
        line.split("=", 1)[0] for line in env_example_text.splitlines() if "=" in line
    )
    assert not missing, f"missing env vars in .env.example: {missing}"


def test_env_example_no_real_secrets(env_example_text):
    """`.env.example` 不含真实 secret（应都是占位符）。"""
    # 简单启发式：默认值不应像真密码（不是 32 字符随机）
    suspicious_patterns = [
        "AKIA",  # AWS access key prefix
        "ghp_",  # GitHub PAT prefix
        "sk-",   # OpenAI key prefix
    ]
    for pattern in suspicious_patterns:
        assert pattern not in env_example_text, f".env.example 不应含真 secret ({pattern})"


# ── 7. 集成：compose + infra configs 一致性 ──


def test_compose_and_prometheus_target_consistent(compose_data, prometheus_config):
    """Prometheus scrape 的 target 必须与 compose 的服务名一致。"""
    api_targets = []
    for j in prometheus_config.get("scrape_configs", []):
        if j.get("job_name") == "rag-api":
            api_targets = j["static_configs"][0]["targets"]
    assert "api:8080" in api_targets
    # compose 里 api 服务暴露 8080
    api_ports = compose_data["services"]["api"]["ports"]
    assert any("8080:8080" in str(p) for p in api_ports)
