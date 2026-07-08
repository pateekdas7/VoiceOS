"""DLQHandler — dead-letter queue routing for events that exhaust retries.

Poison messages must never block a consumer group indefinitely (V3 Ch3
§3.15). After a bounded number of processing failures, DLQHandler moves the
raw stream entry — with full failure context — to a sibling ``dlq:{stream}``
stream for later inspection and manual/automated replay.

Architecture: V3 Ch3 §3.8 (DLQ component), §3.12 (DLQ policy), §3.15/3.16.
"""

from __future__ import annotations

from typing import Any

from .metrics import record_dlq_depth

_DLQ_PREFIX = "dlq:"
DEFAULT_MAX_RETRIES = 3


class DLQHandler:
    """Routes exhausted-retry stream entries to their dead-letter stream.

    Usage:
        dlq = DLQHandler(redis_client, stream="voiceos-events")
        dlq.route_to_dlq(entry_id, fields, reason="handler raised ValueError", attempt_count=3)
        dlq.depth()  # -> current DLQ stream length
    """

    def __init__(self, redis: Any, stream: str, max_retries: int = DEFAULT_MAX_RETRIES) -> None:
        """
        Args:
            redis: A Redis-compatible client (``redis.Redis`` or ``FakeRedisClient``).
            stream: The source stream name (DLQ stream is ``dlq:{stream}``).
            max_retries: Number of processing attempts before an event is
                         eligible for the DLQ (V3 Ch3 §3.12 default: 3).
        """
        self._redis = redis
        self._stream = stream
        self.max_retries = max_retries

    @property
    def dlq_stream_name(self) -> str:
        return f"{_DLQ_PREFIX}{self._stream}"

    def route_to_dlq(
        self,
        entry_id: str,
        fields: dict[Any, Any],
        reason: str,
        attempt_count: int,
    ) -> str:
        """Append the failed entry to the DLQ stream with failure context.

        Args:
            entry_id: The original stream entry ID.
            fields: The original entry's field map (bytes or str keys/values).
            reason: Human-readable failure reason (e.g. the last exception).
            attempt_count: How many processing attempts were made.

        Returns:
            The new entry ID within the DLQ stream.
        """
        payload: dict[str, Any] = {(k.decode() if isinstance(k, bytes) else k): v for k, v in fields.items()}
        payload["_dlq_original_id"] = entry_id
        payload["_dlq_original_stream"] = self._stream
        payload["_dlq_reason"] = reason
        payload["_dlq_attempt_count"] = str(attempt_count)

        new_id = self._redis.xadd(self.dlq_stream_name, payload)
        record_dlq_depth(self._stream, self.depth())
        return new_id.decode() if isinstance(new_id, bytes) else str(new_id)

    def depth(self) -> int:
        """Current length of the DLQ stream."""
        return int(self._redis.xlen(self.dlq_stream_name))
