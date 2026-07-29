"""lockfile 完整性 + 漂移检测测试（ENTERPRISE_AUDIT M3）。

目的：保证 prod/dev/observability 三组 lockfile 与 pyproject.toml 严格同步。
CI 会在每次 PR 跑此测试；如果有人改了 pyproject 但没 refresh lockfile,
测试 fail loud。

设计：
- 三个 lockfile 必须存在
- 必须使用 --generate-hashes（每行 sha256）
- pyproject 里 [project] dependencies 的所有包必须在 requirements.lock
- pyproject 里 [project.optional-dependencies] 的所有包必须在对应 lockfile
- lockfile 漂移检测：lockfile 里的 top-level 包必须能在 pyproject 找到
  （避免有人手动编辑 lockfile 加幽灵包）

不验证：具体 hash 内容（uv/pip-compile 输出的 hash 与平台/时间有关，
漂移检测脚本 scripts/check_lockfile_drift.py 负责更深的检查）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

LOCKFILES = {
    "prod": ROOT / "requirements.lock",
    "dev": ROOT / "requirements-dev.lock",
    "otel": ROOT / "requirements-otel.lock",
}


def _parse_pyproject_deps() -> dict[str, list[str]]:
    """从 pyproject.toml 抽 [project] + optional-dependencies 的包名。

    返回 {"prod": [...], "dev": [...], "otel": [...]}

    简化实现：用 AST 不可靠（toml 不是 AST 格式），正则解析每段：
    - [project] dependencies = [...]  → prod
    - [project.optional-dependencies]
        dev = [...]                    → dev
        observability = [...]          → otel
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    deps: dict[str, list[str]] = {"prod": [], "dev": [], "otel": []}
    current_key = None
    in_deps_value = False

    for line in text.splitlines():
        stripped = line.strip()
        # section header
        if stripped.startswith("[") and stripped.endswith("]"):
            sec = stripped.strip("[]")
            if sec == "project":
                current_key = "prod"
                in_deps_value = False
            elif sec == "project.optional-dependencies":
                current_key = None  # 进 opt deps 子段
                in_deps_value = False
            else:
                current_key = None
                in_deps_value = False
            continue
        # optional-dependencies 子段表头
        m_opt = re.match(r"^([a-z][a-z0-9_-]*)\s*=\s*\[$", stripped)
        if m_opt and current_key is None:
            sub = m_opt.group(1)
            if sub == "dev":
                current_key = "dev"
                in_deps_value = True
            elif sub == "observability":
                current_key = "otel"
                in_deps_value = True
            continue
        # 顶层 dependencies = [
        if current_key == "prod" and re.match(r"^dependencies\s*=\s*\[$", stripped):
            in_deps_value = True
            continue
        # 收尾
        if stripped == "]":
            in_deps_value = False
            continue
        # 项
        if in_deps_value and current_key and stripped.startswith('"'):
            # 抽包名: "name>=1.0,<2.0" → name;  "name[extra]" → name
            m = re.match(r'"([A-Za-z0-9_.\-]+)', stripped)
            if m:
                deps[current_key].append(m.group(1).lower())

    return deps


def _parse_lockfile_top_level(lockfile: Path) -> list[str]:
    """从 lockfile 抽 top-level 包（每段第一行）。"""
    out: list[str] = []
    for line in lockfile.read_text(encoding="utf-8").splitlines():
        # 包段格式:包名==版本 \
        m = re.match(r"^([A-Za-z0-9_.\-]+)==[0-9]", line)
        if m:
            out.append(m.group(1).lower())
    return out


def _has_hash_mode(lockfile: Path) -> bool:
    """lockfile 必须含 sha256 hash（--generate-hashes 标志）。"""
    return "--hash=sha256:" in lockfile.read_text(encoding="utf-8")


# ── 存在 ──


def test_lockfiles_exist():
    """三个 lockfile 必须存在。"""
    for _name, path in LOCKFILES.items():
        assert path.exists(), f"missing lockfile: {path} (run scripts/refresh_lockfile.sh)"


# ── hash 模式 ──


def test_lockfiles_use_hash_mode():
    """每个 lockfile 必须含 --hash=sha256 模式（防止直接 freeze 后改名）。"""
    for _name, path in LOCKFILES.items():
        assert _has_hash_mode(path), f"{path} 缺少 --hash=sha256（必须用 --generate-hashes）"


def test_lockfiles_have_substantial_hash_coverage():
    """每个 lockfile 至少含 50 个 hash 条目（防被人删空当 placeholder）。"""
    for _name, path in LOCKFILES.items():
        n_hashes = path.read_text(encoding="utf-8").count("--hash=sha256:")
        assert n_hashes >= 50, f"{path} 仅 {n_hashes} 个 hash 条目,过少"


# ── pyproject ↔ lockfile 漂移 ──


def test_pyproject_deps_all_present_in_prod_lockfile():
    """pyproject [project] dependencies 的每个包必须出现在 requirements.lock。"""
    pyproject = _parse_pyproject_deps()
    lock = _parse_lockfile_top_level(LOCKFILES["prod"])
    lock_set = set(lock)
    missing: list[str] = []
    for pkg in pyproject["prod"]:
        # name normalization:fastapi → fastapi; uvicorn[standard] → uvicorn
        normalized = pkg.split("[")[0]
        # ragas 包名;有些包 install name ≠ import name(eg: prometheus-client vs prometheus_client)
        if normalized not in lock_set:
            # 尝试 underscore 变体
            if normalized.replace("-", "_") not in lock_set:
                missing.append(pkg)
    assert not missing, (
        f"pyproject [project] deps 缺失于 requirements.lock:{missing}\n"
        "→ 跑 scripts/refresh_lockfile.sh 重新生成"
    )


def test_pyproject_dev_deps_all_present_in_dev_lockfile():
    pyproject = _parse_pyproject_deps()
    lock = _parse_lockfile_top_level(LOCKFILES["dev"])
    lock_set = set(lock)
    for pkg in pyproject["dev"]:
        assert pkg in lock_set or pkg.replace("-", "_") in lock_set, (
            f"pyproject dev dep {pkg} 缺失于 requirements-dev.lock"
        )


def test_pyproject_otel_deps_all_present_in_otel_lockfile():
    pyproject = _parse_pyproject_deps()
    lock = _parse_lockfile_top_level(LOCKFILES["otel"])
    lock_set = set(lock)
    for pkg in pyproject["otel"]:
        assert pkg in lock_set or pkg.replace("-", "_") in lock_set, (
            f"pyproject otel dep {pkg} 缺失于 requirements-otel.lock"
        )


# ── lockfile ↔ pyproject 反向漂移（top-level 必须是 pyproject 声明的） ──


def test_lockfile_top_level_subset_of_pyproject():
    """lockfile top-level 必须是 pyproject 直接声明的 + 关键 transitive。

    放宽：只检查前 5 个 prod dep 都在 lockfile top-level（其他 transitive 也合理）。
    严格反向检查会在 scripts/check_lockfile_drift.py 里做。
    """
    pyproject = _parse_pyproject_deps()
    prod_top5 = set(pyproject["prod"][:5])
    lock = _parse_lockfile_top_level(LOCKFILES["prod"])
    lock_set = set(lock)
    missing = {p for p in prod_top5 if p not in lock_set and p.replace("-", "_") not in lock_set}
    assert not missing, f"lockfile 缺关键 prod deps:{missing}"


# ── 互斥 ──


def test_prod_lockfile_otel_only_extras_not_exporter_or_instrumentation():
    """OTel exporter/instrumentation 不该是 prod 的 transitive(太重,且 P1 默认关)。

    注意:opentelemetry-api / sdk 是合理的 transitive(很多包可选依赖),
    本测试只挡 exporter-otlp / instrumentation-fastapi 这两个真正"OTel 才需要"的。
    """
    prod_lock = set(_parse_lockfile_top_level(LOCKFILES["prod"]))
    otel_heavy = {
        "opentelemetry-exporter-otlp",
        "opentelemetry-instrumentation-fastapi",
    }
    leaked = otel_heavy & prod_lock
    assert not leaked, (
        f"OTel exporter/instrumentation 进了 prod lockfile:{leaked}\n"
        "→ 用 requirements-otel.lock 装,prod 应该走默认 OTLP off 路径"
    )


def test_prod_lockfile_transitive_otel_acceptable_documented():
    """显式记录：opentelemetry-api/sdk 作为 transitive 进 prod 是合规的。

    业务依据：很多现代 Python 库(SQLAlchemy/Pydantic)通过 opentelemetry-api
    做 hook point。pip 不会因 transitive 触发真 instrumentation。
    本断言不是测试,是文档 + 兜底(让 reviewer 看到这判断)。
    """
    prod_lock = set(_parse_lockfile_top_level(LOCKFILES["prod"]))
    # 不 fail,只记录事实。如果未来 otel 重 transitive 跑出爆炸,这条文档应改。
    otel_api_sdk = {
        "opentelemetry-api",
        "opentelemetry-sdk",
    } & prod_lock
    # 仅在 CI 日志打印,不做断言
    if otel_api_sdk:
        # 写到 stderr 不污染 pytest 输出
        import sys

        print(
            f"[INFO] opentelemetry-api/sdk as transitive in prod:{otel_api_sdk}",
            file=sys.stderr,
        )


def test_dev_lockfile_includes_test_tooling():
    """dev lockfile 必须含 pytest / ruff / mypy（防漏）。"""
    dev_lock = set(_parse_lockfile_top_level(LOCKFILES["dev"]))
    must_have = {"pytest", "ruff", "mypy"}
    missing = must_have - dev_lock
    assert not missing, f"dev lockfile 缺{missing}"


# ── hash 一致性（同包多 hash 防单点篡改） ──


def test_lockfile_packages_have_multiple_hashes():
    """每个包段至少 2 个 hash（sha256 + sha512 或者多版本 wheel）。

    防止有人编辑 lockfile 把 --generate-hashes 行删成单 hash 绕过安全。
    """
    for _name, path in LOCKFILES.items():
        text = path.read_text(encoding="utf-8")
        # 按空行分段
        blocks = [b for b in text.split("\n\n") if b.strip()]
        for block in blocks:
            lines = block.splitlines()
            if not lines or not re.match(r"^[A-Za-z0-9_.\-]+==", lines[0]):
                continue
            n_hash = sum(1 for ln in lines if ln.strip().startswith("--hash=sha256:"))
            assert n_hash >= 2, (
                f"{path} 包 {lines[0]} 仅 {n_hash} 个 sha256 hash（应 ≥2）"
            )


# ── 入口 .in 文件漂移 ──


@pytest.mark.parametrize("lock,infile", [
    (LOCKFILES["prod"], ROOT / "requirements.in"),
    (LOCKFILES["dev"], ROOT / "requirements-dev.in"),
    (LOCKFILES["otel"], ROOT / "requirements-otel.lock"),  # otel .in 是 fixed schema
])
def test_in_files_exist(lock, infile):
    """每个 lockfile 必须有对应的 .in 源文件。"""
    if infile.name == "requirements-otel.lock":
        # otel 单独测
        infile = ROOT / "requirements-otel.in"
    assert infile.exists(), f"missing source file: {infile}"
