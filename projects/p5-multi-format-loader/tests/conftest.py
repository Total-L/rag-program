"""P5 pytest 配置：让 tests/ 下能被 import。

确保 ``from src.X import ...`` 在 pytest 任意层级收集时都能找到 src/。
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
