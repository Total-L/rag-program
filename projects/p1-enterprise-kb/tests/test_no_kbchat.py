"""P1 自包含回归：静态扫描 src/ 下的 kbchat / rag-course 引用。

ADR-0005：P1 企业程序本体不许 import kbchat、不许硬编码 /rag-course 路径。
本测试在 CI 中守护这条边界 —— 任何新增的 `from kbchat...` / `import kbchat`
或 `"/Users/totallai/rag-course"` 都会让这个测试红。
"""

from __future__ import annotations

import re
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"

# 允许的例外：_compat.py 自身的 docstring 解释历史；本文件本身（测试守护）
_ALLOWED_FILES = {"_compat.py", __file__.rsplit("/", 1)[-1]}

_FORBIDDEN_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+kbchat\s+|import\s+kbchat\b|import\s+rag_course\b|from\s+rag_course\s+)",
    re.MULTILINE,
)
_FORBIDDEN_PATH_RE = re.compile(r"/Users/totallai/rag-course")


def _scan_py(path: Path) -> list[str]:
    """返回该文件里的违规行（空 list = 无违规）。"""
    text = path.read_text(encoding="utf-8", errors="ignore")
    bad: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        if _FORBIDDEN_IMPORT_RE.search(line):
            bad.append(f"{path.name}:{i}: {line.strip()}")
        # 路径硬编码仅匹配非注释的代码行（避免 docstring 误伤）
        if _FORBIDDEN_PATH_RE.search(line) and not line.lstrip().startswith("#"):
            # 但要排除 docstring 中的纯说明字符串（最严的判断：行尾是 .md/.py 引用句）
            # 简单规则：只要是字符串字面量中提及也不允许 —— 因为代码里出现这条路径就是硬耦合
            bad.append(f"{path.name}:{i}: {line.strip()}")
    return bad


def test_no_kbchat_or_ragcourse_in_p1_src():
    violations: list[str] = []
    for py in sorted(SRC_DIR.rglob("*.py")):
        if py.name in _ALLOWED_FILES:
            continue
        violations.extend(_scan_py(py))
    assert not violations, (
        "P1 src/ 不许 import kbchat / 硬编码 /rag-course 路径：\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\n如需用 kbchat，请放 course-mirror/ 子模块；如需 P1 自实现工具，请放 _compat.py。"
    )
