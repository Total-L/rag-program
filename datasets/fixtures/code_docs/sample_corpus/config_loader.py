"""YAML config loader demo (no PyYAML dependency).

Loads simple `key: value` files, ignoring comments. Q&A target:
"how to parse simple yaml without external libs?"
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_\.]*)\s*[:=]\s*(.+?)\s*$")


def load_simple_config(path: str | Path) -> dict[str, Any]:
    """Load a flat `key: value` config file into a dict."""
    out: dict[str, Any] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        m = _KV_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        # 试着把数字 / bool 转成原生类型
        if value.lower() in ("true", "false"):
            out[key] = value.lower() == "true"
        else:
            try:
                out[key] = int(value)
            except ValueError:
                try:
                    out[key] = float(value)
                except ValueError:
                    out[key] = value
    return out
