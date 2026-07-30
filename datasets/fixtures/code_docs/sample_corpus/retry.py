"""Retry decorator with exponential backoff.

Usage:
    @retry(max_attempts=3, base_delay=0.5)
    def fetch(): ...

A typical Q&A target for RAG-on-code: "how do I retry with backoff?"
"""
from __future__ import annotations

import functools
import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")


def retry(
    *,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Return a decorator that retries on the given exceptions."""

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            attempt = 0
            while True:
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    attempt += 1
                    if attempt >= max_attempts:
                        raise
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    delay += random.random() * 0.1
                    time.sleep(delay)

        return wrapper

    return decorator
