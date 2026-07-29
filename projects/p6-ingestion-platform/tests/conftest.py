"""P6 pytest 配置：注册 --run-pg 开关。

本机没有 Docker/Postgres，pgvector 端到端测试默认 skip。
有 Postgres 的环境：
    P6_PG_DSN=postgresql://... pytest --run-pg
"""

from __future__ import annotations


def pytest_addoption(parser):
    parser.addoption(
        "--run-pg",
        action="store_true",
        default=False,
        help="跑需要真实 Postgres+pgvector 的测试（默认 skip）",
    )
