"""EventBus — the immutable, ordered, replayable event stream (V3 Ch3).

Backed by Redis Streams (XADD/XREADGROUP): durable append, consumer-group
fan-out, at-least-once delivery, and replay. This module owns the raw
stream mechanics (append, decode, replay, DLQ delegation); Publisher wraps
``publish()`` with envelope construction, and Consumer wraps subscription +
delivery semantics on top of ``ensure_consumer_group()``/reads.

Architecture: V3 Ch3 §3.7 (EventBus public interface), §3.8 (components),
              §3.13 (backend: redis_streams).
"""

from __future__ import annotations

from typing import Any

from src.libs.contracts.events.envelope import EventEnvelope

from .dlq import DEFAULT_MAX_RETRIES, DLQHandler
from .metrics import record_published

DEFAULT_STREAM = "voiceos-events"


class EventBus:
    """Redis Streams-backed event bus: append, replay, and DLQ delegation.

    Usage:
        bus = EventBus(redis_client)
        bus.ensure_consumer_group("main-group")
        bus.publish(envelope)                       # XADD
        events = bus.replay_from()                   # full history
    """

    def __init__(
        self,
        redis: Any,
        stream: str = DEFAULT_STREAM,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        """
        Args:
            redis: A Redis-compatible client (``redis.Redis`` or ``FakeRedisClient``).
            stream: The event stream name (V3 Ch3 §3.13 partition_key: call_id
                    conceptually; VoiceOS uses one platform stream per
                    environment with ``tenant_id``/``call_id`` carried in the
                    envelope, consistent with the existing single-Redis
                    deployment — see Sprint-013 ADR note in CHANGELOG).
            max_retries: Failures before an event is DLQ-routed.
        """
        self._redis = redis
        self._stream = stream
        self._dlq = DLQHandler(redis, stream, max_retries=max_retries)
        self.max_retries = max_retries

    @property
    def stream_name(self) -> str:
        return self._stream

    @property
    def dlq_stream_name(self) -> str:
        return self._dlq.dlq_stream_name

    # ------------------------------------------------------------------
    # Publish / append
    # ------------------------------------------------------------------

    def publish(self, envelope: EventEnvelope) -> str:
        """Durably append ``envelope`` to the stream. Returns the entry ID."""
        fields = {"envelope": envelope.model_dump_json()}
        entry_id = self._redis.xadd(self._stream, fields)
        record_published(envelope.event_type, self._stream)
        return entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)

    # ------------------------------------------------------------------
    # Consumer group management
    # ------------------------------------------------------------------

    def ensure_consumer_group(self, group: str) -> None:
        """Idempotently create ``group`` on this stream, from the beginning.

        Equivalent to ``XGROUP CREATE <stream> <group> 0 MKSTREAM``. Safe to
        call on every service startup — a pre-existing group raises
        BUSYGROUP, which is swallowed.
        """
        try:
            self._redis.xgroup_create(self._stream, group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    # ------------------------------------------------------------------
    # Replay (crash recovery, V3 Ch3 §3.7 replay())
    # ------------------------------------------------------------------

    def replay_from(self, offset: str = "-", count: int | None = None) -> list[tuple[str, EventEnvelope]]:
        """Replay stream entries from ``offset`` (default: full history).

        Args:
            offset: Starting entry ID, Redis range syntax (default ``"-"``
                    = beginning). Use an entry ID to resume from a point.
            count: Optional maximum number of entries to return.
        """
        raw = self._redis.xrange(self._stream, min=offset, max="+", count=count)
        return [(self._entry_id_str(entry_id), self.decode_envelope(fields)) for entry_id, fields in raw]

    # ------------------------------------------------------------------
    # DLQ delegation
    # ------------------------------------------------------------------

    def route_to_dlq(self, entry_id: str, fields: dict[Any, Any], reason: str, attempt_count: int) -> str:
        return self._dlq.route_to_dlq(entry_id, fields, reason=reason, attempt_count=attempt_count)

    def dlq_depth(self) -> int:
        return self._dlq.depth()

    # ------------------------------------------------------------------
    # Decoding helpers
    # ------------------------------------------------------------------

    @staticmethod
    def decode_envelope(fields: dict[Any, Any]) -> EventEnvelope:
        """Decode a raw stream entry's fields back into an EventEnvelope."""
        raw = fields.get(b"envelope", fields.get("envelope"))
        if raw is None:
            raise ValueError("Stream entry is missing the 'envelope' field")
        raw_str = raw.decode() if isinstance(raw, bytes) else raw
        return EventEnvelope.model_validate_json(raw_str)

    @staticmethod
    def _entry_id_str(entry_id: Any) -> str:
        return entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
