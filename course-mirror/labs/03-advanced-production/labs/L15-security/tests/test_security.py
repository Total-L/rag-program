"""L15 Security 单测：5 类攻击 → 必须拦截。"""
from __future__ import annotations

import sys
from pathlib import Path

# 让 run.py 可 import
sys.path.insert(0, str(Path(__file__).parent.parent))
from run import is_allowed, has_path_traversal, safe_obsidian_uri  # noqa: E402


def test_acl_excludes_sensitive_categories():
    assert not is_allowed("/Users/x/notes/01-投资/持仓/持仓概览.md")
    assert not is_allowed("/Users/x/notes/01-投资/黑名单.md")
    assert not is_allowed("/Users/x/notes/99-配置/secrets.yaml")
    assert is_allowed("/Users/x/notes/02-编程笔记/python.md")
    assert is_allowed("/Users/x/notes/HR/年假.md")


def test_path_traversal_blocked():
    assert has_path_traversal("../../../etc/passwd")
    assert has_path_traversal("/etc/passwd")
    assert has_path_traversal("~/secrets")
    assert not has_path_traversal("normal/path.md")
    assert not has_path_traversal("财务/报销.md")


def test_obsidian_uri_injection_blocked():
    # 正常路径
    uri = safe_obsidian_uri("/Users/x/vault", "财务/报销.md")
    assert uri.startswith("obsidian://open?path=")
    # 注入尝试
    for bad in ["../escape.md", "/etc/passwd", "normal.md;rm -rf /"]:
        try:
            safe_obsidian_uri("/Users/x/vault", bad)
            assert False, f"应该拦截: {bad}"
        except ValueError:
            pass


def test_acl_pure_python():
    """不依赖任何外部库，仅 stdlib。"""
    import re
    patterns = [
        r"^.*/01-投资/持仓/.*",
        r"^.*/01-投资/黑名单.*",
        r"^.*/99-配置/.*",
    ]
    sensitive = "/Users/x/notes/01-投资/持仓/持仓概览.md"
    assert any(re.match(p, sensitive) for p in patterns)
