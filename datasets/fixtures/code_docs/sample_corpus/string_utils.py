"""String helpers for the P2 demo corpus.

Public API:
- slugify: lower-cased, hyphen-separated ascii slug
- truncate: cut with ellipsis
- count_words: word count for latin + cjk
"""
from __future__ import annotations

import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_CJK_RE = re.compile(r"[一-鿿]")


def slugify(s: str) -> str:
    """Return a url-safe lowercase slug of `s`."""
    s = s.lower().strip()
    return _SLUG_RE.sub("-", s).strip("-")


def truncate(s: str, n: int = 80, suffix: str = "…") -> str:
    """Cut `s` to at most `n` chars; append `suffix` if truncated."""
    if len(s) <= n:
        return s
    return s[: max(0, n - len(suffix))] + suffix


def count_words(s: str) -> int:
    """Count latin words + cjk chars (1 char ≈ 1 word)."""
    latin = len(re.findall(r"\b\w+\b", s))
    cjk = len(_CJK_RE.findall(s))
    return latin + cjk
