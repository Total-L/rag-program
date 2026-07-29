"""Projects 根 conftest：解决跨项目 src.* 命名空间冲突。

各项目的 ``src/`` 目录名相同，pytest 一起收集时 sys.modules["src"] 会污染。
在每个收集节点开始前 evict 一次。
"""

from __future__ import annotations

import sys


def _evict_src_cache() -> None:
    for k in [k for k in sys.modules if k == "src" or k.startswith("src.")]:
        del sys.modules[k]


def pytest_collectstart(collector):
    """每个 node 开始收集前 evict 一次 src 缓存。"""
    _evict_src_cache()
