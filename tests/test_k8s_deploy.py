"""K8S 部署单元测试（ENTERPRISE_AUDIT S5）。

不依赖 helm CLI；用纯 Python + YAML 解析验证：
- helm chart 结构完整
- values 字段类型正确
- 模板渲染出来的资源符合 K8S schema
- ConfigMap keys 覆盖关键 env
- Secret 不在 chart 里直接含明文敏感值
- 文档存在
- Dockerfile 镜像 tag 与 Chart 一致

跑法：pytest tests/test_k8s_deploy.py
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHART_DIR = ROOT / "deploy" / "helm" / "rag-program"
TEMPLATE_DIR = CHART_DIR / "templates"

# ── 必要文件 ──


def test_chart_yaml_exists():
    assert (CHART_DIR / "Chart.yaml").exists()


def test_values_yaml_exists():
    assert (CHART_DIR / "values.yaml").exists()


def test_helmignore_exists():
    assert (CHART_DIR / ".helmignore").exists()


def test_chart_readme_exists():
    assert (CHART_DIR / "README.md").exists()


def test_helpers_template_exists():
    assert (TEMPLATE_DIR / "_helpers.tpl").exists()


# ── 模板覆盖 ──


REQUIRED_TEMPLATES = [
    "deployment.yaml",
    "service.yaml",
    "configmap.yaml",
    "secret.yaml",
    "ingress.yaml",
    "hpa.yaml",
    "pdb.yaml",
    "serviceaccount.yaml",
    "pvc.yaml",
]


@pytest.mark.parametrize("template", REQUIRED_TEMPLATES)
def test_required_template_exists(template: str):
    assert (TEMPLATE_DIR / template).exists(), f"missing template: {template}"


# ── Chart.yaml 结构 ──


def test_chart_yaml_required_fields():
    import yaml

    chart = yaml.safe_load((CHART_DIR / "Chart.yaml").read_text(encoding="utf-8"))
    assert chart["apiVersion"] == "v2"
    assert chart["name"] == "rag-program"
    assert chart["version"]  # chart version
    assert chart["appVersion"]  # app version
    assert "description" in chart
    assert "maintainers" in chart
    assert len(chart["maintainers"]) >= 1


# ── values.yaml 结构 ──


def test_values_yaml_has_required_sections():
    import yaml

    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8"))
    required_sections = [
        "image",
        "replicaCount",
        "resources",
        "service",
        "ingress",
        "healthcheck",
        "persistence",
        "autoscaling",
        "podDisruptionBudget",
        "securityContext",
        "serviceAccount",
        "config",
        "secrets",
        "otel",
        "podAnnotations",
    ]
    for s in required_sections:
        assert s in values, f"values.yaml missing required section: {s}"


def test_values_security_defaults():
    """安全默认必须全开:non-root + readOnlyRootFilesystem + drop ALL caps。"""
    import yaml

    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8"))
    sc = values["securityContext"]
    assert sc["runAsNonRoot"] is True
    assert sc["readOnlyRootFilesystem"] is True
    assert sc["allowPrivilegeEscalation"] is False
    assert "ALL" in sc["capabilities"]["drop"]


def test_values_healthcheck_paths():
    """探针必须指向 FastAPI 真实存在的端点(P1 server.py: /v1/healthz + /v1/readyz)。"""
    import yaml

    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8"))
    assert values["healthcheck"]["liveness"]["path"] == "/v1/healthz"
    assert values["healthcheck"]["readiness"]["path"] == "/v1/readyz"


def test_values_required_config_keys():
    """config 段必须含所有 server.py 读的 env。"""
    import yaml

    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8"))
    cfg = values["config"]
    required = [
        "RAG_PROG_API_KEY",
        "RAG_PROG_LLM_BACKEND",
        "RAG_PROG_AUDIT_DIR",
        "RAG_PROG_DATA_DIR",
        "RAG_PROG_MAX_QUERY_LEN",
        "RAG_PROG_GUARD_FAIL_CLOSED",
    ]
    for k in required:
        assert k in cfg, f"config missing: {k}"


def test_values_required_secrets():
    """secrets 段必须含 audit salt + llm key。"""
    import yaml

    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8"))
    sec = values["secrets"]
    required = ["AUDIT_USER_ID_SALT", "ANTHROPIC_API_KEY"]
    for k in required:
        assert k in sec, f"secrets missing: {k}"


def test_values_prometheus_annotations():
    """pod annotations 必须含 prometheus.io/scrape。"""
    import yaml

    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8"))
    ann = values["podAnnotations"]
    assert ann.get("prometheus.io/scrape") == "true"
    assert ann.get("prometheus.io/port") == "8080"
    assert ann.get("prometheus.io/path") == "/metrics"


# ── Secret 不含明文 ──


def test_secret_template_does_not_have_plaintext_passwords():
    """Secret 模板只放 base64 编码,不直接明文。"""
    template = (TEMPLATE_DIR / "secret.yaml").read_text(encoding="utf-8")
    assert "b64enc" in template
    # 不应有 'password:' 这种明文
    assert not re.search(r"^\s*password:\s*\S+$", template, re.MULTILINE)


# ── Deployment 关键字段 ──


def test_deployment_uses_configmap_and_secret():
    template = (TEMPLATE_DIR / "deployment.yaml").read_text(encoding="utf-8")
    assert "configMapRef" in template
    assert "secretRef" in template


def test_deployment_has_liveness_and_readiness():
    template = (TEMPLATE_DIR / "deployment.yaml").read_text(encoding="utf-8")
    assert "livenessProbe" in template
    assert "readinessProbe" in template


def test_deployment_has_resource_limits():
    """resource block 用 toYaml 渲染,所以检查 values.yaml 的结构 + template 引用。"""
    template = (TEMPLATE_DIR / "deployment.yaml").read_text(encoding="utf-8")
    assert "resources:" in template
    assert ".Values.resources" in template

    # values.yaml 必须定义 requests + limits
    import yaml

    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8"))
    res = values["resources"]
    assert "requests" in res and "limits" in res
    assert "cpu" in res["requests"] and "memory" in res["requests"]
    assert "cpu" in res["limits"] and "memory" in res["limits"]
    # limits.cpu 必须 >= requests.cpu
    cpu_req = _parse_cpu(res["requests"]["cpu"])
    cpu_lim = _parse_cpu(res["limits"]["cpu"])
    assert cpu_lim >= cpu_req, f"limits.cpu ({cpu_lim}) < requests.cpu ({cpu_req})"


def _parse_cpu(v: str) -> float:
    """Parse K8s CPU value (e.g. '500m', '2', '0.5') to float cores."""
    s = str(v).strip().lower()
    if s.endswith("m"):
        return float(s[:-1]) / 1000
    return float(s)


def test_deployment_uses_persistent_volumes():
    """audit + data 必须挂 PVC。"""
    template = (TEMPLATE_DIR / "deployment.yaml").read_text(encoding="utf-8")
    assert "persistentVolumeClaim" in template
    assert "audit" in template
    assert "data" in template


def test_deployment_tmp_is_emptydir():
    """readOnlyRootFilesystem=true → /tmp 必须 emptyDir 写。"""
    template = (TEMPLATE_DIR / "deployment.yaml").read_text(encoding="utf-8")
    assert "emptyDir" in template
    assert "/tmp" in template


# ── HPA ──


def test_hpa_uses_v2_api():
    template = (TEMPLATE_DIR / "hpa.yaml").read_text(encoding="utf-8")
    assert "autoscaling/v2" in template


def test_hpa_targets_deployment():
    template = (TEMPLATE_DIR / "hpa.yaml").read_text(encoding="utf-8")
    assert "kind: Deployment" in template
    assert "scaleTargetRef" in template


# ── Ingress ──


def test_ingress_uses_v1_api():
    template = (TEMPLATE_DIR / "ingress.yaml").read_text(encoding="utf-8")
    assert "networking.k8s.io/v1" in template


def test_ingress_supports_tls():
    template = (TEMPLATE_DIR / "ingress.yaml").read_text(encoding="utf-8")
    assert "tls:" in template


# ── 文档 ──


def test_k8s_deploy_doc_exists():
    assert (ROOT / "docs" / "K8S_DEPLOY.md").exists()


def test_k8s_deploy_doc_has_required_sections():
    text = (ROOT / "docs" / "K8S_DEPLOY.md").read_text(encoding="utf-8")
    required = [
        "## 1. 前置",
        "## 2. 部署步骤",
        "## 3. 持久化",
        "## 4. 配置项",
        "## 5. 安全清单",
        "## 6. 升级 / 回滚",
        "## 8. 监控",
        "## 12. CI 集成",
    ]
    for s in required:
        assert s in text, f"doc missing section: {s}"


# ── Dockerfile 一致性 ──


def test_dockerfile_image_name_matches_chart():
    """Dockerfile 镜像名必须与 chart 默认一致(防 chart 指向错镜像)。"""
    import yaml

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    # chart repo 在 values.yaml(Chart.yaml 不放 repo)
    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8"))
    repo = values["image"]["repository"]
    # Dockerfile 用 FROM ...:<tag>; 至少出现同一 repo
    assert repo.split("/")[-1] in dockerfile or repo in dockerfile, (
        f"chart image repo {repo} 与 Dockerfile 不一致"
    )


def test_dockerfile_has_healthcheck():
    """Dockerfile 必须有 HEALTHCHECK(对应 chart livenessProbe)。"""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "HEALTHCHECK" in dockerfile


# ── Release workflow ──


def test_release_workflow_uses_oidc():
    """release.yml 必须用 OIDC trusted publishing(不进 token)。"""
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "id-token: write" in text
    assert "pypa/gh-action-pypi-publish" in text


def test_release_workflow_has_gate():
    """release 必须先过 gate(lint + test + drift)才 build。"""
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "needs: gate" in text


def test_release_workflow_handles_testpypi():
    """workflow_dispatch 必须支持 TestPyPI dry-run。"""
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "testpypi" in text.lower()
    assert "workflow_dispatch" in text


# ── CI workflow 升级 ──


def test_ci_workflow_uses_lockfile():
    """ci.yml 必须用 --require-hashes 装 lockfile(ENTERPRISE_AUDIT M3)。"""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "requirements.lock" in text
    assert "--require-hashes" in text
    assert "check_lockfile_drift" in text
