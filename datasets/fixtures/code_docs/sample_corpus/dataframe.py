"""Tiny in-memory dataframe for the P2 demo.

A stripped-down DataFrame-like with column-wise storage. Demonstrates
dunder methods + vectorized ops — a good AST chunking target.
"""
from __future__ import annotations

from typing import Any, Iterable


class TinyDataFrame:
    """Column-wise tabular data structure."""

    def __init__(self, data: dict[str, list[Any]]) -> None:
        if not data:
            self._columns: dict[str, list[Any]] = {}
            self._n = 0
            return
        n = len(next(iter(data.values())))
        for k, v in data.items():
            if len(v) != n:
                raise ValueError(f"column {k!r} length {len(v)} != {n}")
        self._columns = dict(data)
        self._n = n

    @property
    def columns(self) -> list[str]:
        return list(self._columns.keys())

    def __len__(self) -> int:
        return self._n

    def __getitem__(self, key: str) -> list[Any]:
        return self._columns[key]

    def head(self, n: int = 5) -> "TinyDataFrame":
        return TinyDataFrame({k: v[:n] for k, v in self._columns.items()})

    def sum(self) -> dict[str, float]:
        return {k: _safe_sum(v) for k, v in self._columns.items()}

    def filter(self, col: str, pred) -> "TinyDataFrame":
        keep = [i for i, v in enumerate(self._columns[col]) if pred(v)]
        return TinyDataFrame({k: [v[i] for i in keep] for k, v in self._columns.items()})


def _safe_sum(xs: Iterable[Any]) -> float:
    s = 0.0
    for x in xs:
        if isinstance(x, (int, float)):
            s += float(x)
    return s
