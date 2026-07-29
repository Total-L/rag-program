"""P1 pytest 配置：让 ``from src.X import ...`` 在所有 tests/ 文件中能解析。

之前每个 test 文件自己 ``sys.path.insert(0, ...)``，模块级副作用会**全局污染 sys.path**，
当 ``pytest projects/`` 把 p1 和 p5 一起收集时，p5 的 ``from src.X import`` 找到的是 p1 的 src。
现在统一在 conftest 里 setup + teardown。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent  # projects/p1-enterprise-kb/

P1_PATHS = [
    str(PROJECT_ROOT / "src"),
    str(PROJECT_ROOT / "clients" / "python"),
]


def pytest_configure(config):
    """pytest 启动时插入 p1 专属路径到 sys.path（用 tryfirst 优先执行）。"""
    for p in P1_PATHS:
        if p not in sys.path:
            sys.path.insert(0, p)


def pytest_unconfigure(config):
    """pytest 退出前清掉，避免污染下一个 project 的 import。"""
    for p in P1_PATHS:
        while p in sys.path:
            sys.path.remove(p)
    # Clean up leftover src.* modules (in case p1's src was imported elsewhere)
    to_remove = [k for k in sys.modules if k == "src" or k.startswith("src.")]
    for k in to_remove:
        del sys.modules[k]  # noqa: F821


@pytest.fixture(autouse=True)
def _ensure_metrics_loaded():
    """S2 后 metrics 加 tenant_id label —— observability 模块 import 时已建好。

    这里仅确保被 test_server 直接 `from observability import rag_queries_total`
    拿到的对象与 pipeline.ask_agentic 内部用的是同一对象（共享 module cache）。
    """
    try:
        import observability  # noqa: F401 — 触发模块级 metric 创建
    except ImportError:
        pass
    yield
