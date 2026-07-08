"""EventRouter — maps event_type to registered subscriber handlers.

A thin in-process registry consulted by Consumer.subscribe()/start(). Keeps
routing logic (which handlers care about which event_type) separate from
delivery mechanics (XREADGROUP polling, dedup, retry — see consumer.py).

Architecture: V3 Ch3 §3.8 (Pub/Sub dispatcher → Consumer groups).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

EventHandler = Callable[[dict[str, Any]], None]
"""A handler receives the decoded EventEnvelope payload dict."""


class EventRouter:
    """Registry of event_type -> [handler, ...].

    Multiple handlers may subscribe to the same event_type; all are invoked
    in registration order. A handler raising propagates to the caller
    (Consumer applies retry/backoff/DLQ semantics around each handler call).
    """

    def __init__(self) -> None:
        self._routes: dict[str, list[EventHandler]] = {}

    def register(self, event_type: str, handler: EventHandler) -> None:
        """Register ``handler`` to be invoked for events of ``event_type``."""
        self._routes.setdefault(event_type, []).append(handler)

    def handlers_for(self, event_type: str) -> list[EventHandler]:
        """Return the handlers registered for ``event_type`` (empty if none)."""
        return list(self._routes.get(event_type, []))

    def event_types(self) -> list[str]:
        """Return all event_types with at least one registered handler."""
        return list(self._routes.keys())
