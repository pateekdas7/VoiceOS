"""VoiceOS v2 — Event Bus library.

The immutable, ordered, replayable event stream: at-least-once delivery,
consumer-side idempotency, dead-letter queuing, and crash-recovery replay,
backed by Redis Streams. This is the reliability spine every service
publishes state changes through (V3 Ch3).

Architecture: V3 Ch3 (Event Bus Architecture); V6 Ch6 (Event Standards).
"""

from .bus import EventBus
from .consumer import Consumer
from .dedup import EventDeduplicator
from .dlq import DLQHandler
from .publisher import Publisher
from .router import EventRouter

__all__ = [
    "Consumer",
    "DLQHandler",
    "EventBus",
    "EventDeduplicator",
    "EventRouter",
    "Publisher",
]
