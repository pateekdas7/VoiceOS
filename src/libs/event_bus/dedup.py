"""EventDeduplicator — consumer-side idempotency by event_id.

At-least-once delivery (V3 Ch3 §3.12) means a consumer may see the same
event more than once (redelivery after a crash, a retried publish, etc.).
Rather than chase exactly-once delivery, VoiceOS makes redelivery safe: each
``event_id`` is recorded in Redis on first sight (TTL 24h — long enough to
outlast any plausible redelivery window) and subsequent sightings are
short-circuited before the handler runs.

Architecture: V3 Ch3 §3.12 (delivery); V3 Ch8 (Idempotency); V6 Ch6 §6.9.
"""

from __future__ import annotations

from typing import Any

from src.libs.redis_client.ttl_guard import TTLGuard

from .metrics import record_dedup_hit

_KEY_PREFIX = "voiceos:dedup:"
DEDUP_TTL_SECONDS = 86_400  # 24 hours (V3 Ch3 §3.12: dedup by event_id)


class EventDeduplicator:
    """Redis-backed, TTL-bounded dedup check keyed by ``event_id``.

    Usage:
        dedup = EventDeduplicator(redis_client, stream="voiceos-events")
        if dedup.is_duplicate(envelope.event_id):
            return  # already processed — skip the handler
    """

    def __init__(self, redis: Any, stream: str, ttl_seconds: int = DEDUP_TTL_SECONDS) -> None:
        """
        Args:
            redis: A Redis-compatible client (``redis.Redis`` or ``FakeRedisClient``).
            stream: The stream name this deduplicator guards (keys are
                    namespaced per-stream so the same event_id in different
                    streams is tracked independently).
            ttl_seconds: How long a seen event_id is remembered.
        """
        self._redis = redis
        self._ttl_guard = TTLGuard(redis)
        self._stream = stream
        self._ttl_seconds = ttl_seconds

    def is_duplicate(self, event_id: str) -> bool:
        """Record ``event_id`` as seen; return True if it was already seen.

        The check-and-record is a single atomic ``SET NX`` — first caller
        wins and gets False (not a duplicate); every subsequent caller
        within the TTL window gets True.
        """
        key = self._key(event_id)
        newly_set = self._ttl_guard.set(key, b"1", ex=self._ttl_seconds, nx=True)
        is_dup = not bool(newly_set)
        if is_dup:
            record_dedup_hit(self._stream)
        return is_dup

    def _key(self, event_id: str) -> str:
        return f"{_KEY_PREFIX}{self._stream}:{event_id}"
