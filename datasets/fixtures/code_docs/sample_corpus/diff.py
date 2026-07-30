"""Longest common subsequence (LCS) for the P2 demo.

A classic algorithm that appears in RAG-on-code Q&A: "diff two strings"
or "find common subsequence".
"""
from __future__ import annotations


def lcs_length(a: str, b: str) -> int:
    """Return the length of the longest common subsequence of `a` and `b`."""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return 0
    # 使用一行滚动数组
    prev = [0] * (m + 1)
    cur = [0] * (m + 1)
    for i in range(1, n + 1):
        ai = a[i - 1]
        for j in range(1, m + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
            else:
                cur[j] = max(prev[j], cur[j - 1])
        prev, cur = cur, prev
    return prev[m]


def lcs_diff(a: str, b: str) -> list[tuple[str, str]]:
    """Return a list of (op, char) edits to turn `a` into `b`.

    op ∈ {"=", "+", "-"}
    """
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    ops: list[tuple[str, str]] = []
    i, j = n, m
    while i > 0 and j > 0:
        if a[i - 1] == b[j - 1]:
            ops.append(("=", a[i - 1]))
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            ops.append(("-", a[i - 1]))
            i -= 1
        else:
            ops.append(("+", b[j - 1]))
            j -= 1
    while i > 0:
        ops.append(("-", a[i - 1]))
        i -= 1
    while j > 0:
        ops.append(("+", b[j - 1]))
        j -= 1
    ops.reverse()
    return ops
