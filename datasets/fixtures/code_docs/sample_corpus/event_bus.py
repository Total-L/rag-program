"""Synchronous event bus for the P2 demo.

Useful for AST Q&A: "how do I implement pub-sub in Python?"
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable


class EventBus:
    """A simple synchronous publish-subscribe bus."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event: str, handler: Callable) -> None:
        """Register `handler` for `event`."""
        self._handlers[event].append(handler)

    def unsubscribe(self, event: str, handler: Callable) -> None:
        """Remove `handler` from `event` (no-op if not present)."""
        if handler in self._handlers[event]:
            self._handlers[event].remove(handler)

    def publish(self, event: str, *args, **kwargs) -> None:
        """Call all registered handlers for `event`."""
        for h in list(self._handlers[event]):
            h(*args, **kwargs)

    def clear(self, event: str | None = None) -> None:
        """Remove all handlers (for one event or all events)."""
        if event is None:
            self._handlers.clear()
        else:
            self._handlers.pop(event, None)
