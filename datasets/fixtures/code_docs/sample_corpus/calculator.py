"""A tiny calculator for the P2 AST chunking demo.

Functions:
- add: a + b
- subtract: a - b
- multiply: a * b
- divide: a / b (with zero guard)
"""
from __future__ import annotations


def add(a: float, b: float) -> float:
    """Add two numbers and return the sum."""
    return a + b


def subtract(a: float, b: float) -> float:
    """Subtract b from a and return the result."""
    return a - b


def multiply(a: float, b: float) -> float:
    """Multiply two numbers and return the product."""
    return a * b


def divide(a: float, b: float) -> float:
    """Divide a by b. Raises ValueError when b == 0."""
    if b == 0:
        raise ValueError("division by zero")
    return a / b
