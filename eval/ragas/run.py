"""向后兼容 shim(ENTERPRISE_AUDIT C1)。

旧 `eval/ragas/run.py`(手写打分器)已被 `runner.py` 真 RAGAS 替代。
这里转发调用并打印弃用警告,避免破坏老调用。
"""
from __future__ import annotations

import sys
import warnings

if __name__ == "__main__":
    warnings.warn(
        "eval/ragas/run.py is deprecated; use `python -m eval.ragas.runner` "
        "(C1 真 RAGAS, ENTERPRISE_AUDIT)",
        DeprecationWarning,
        stacklevel=2,
    )
    # 直接转发到 runner.main()
    from .runner import main as _runner_main
    sys.argv[0] = "eval.ragas.runner"
    _runner_main()
