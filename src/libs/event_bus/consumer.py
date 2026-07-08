"""Consumer — subscription management and at-least-once delivery (V3 Ch3).

Reads new entries via XREADGROUP, skips events already seen (consumer-side
dedup by event_id, V3 Ch8), dispatches to registered handlers, and ACKs on
success. A handler failure is retried in-process with exponential backoff
(1s, 2s, 4s by default); once the retry budget is exhausted the entry is
routed to the DLQ and ACKed (removed from the pending-entries list) so a
poison message never blocks the consumer group (V3 Ch3 §3.15).

Architecture: V3 Ch3 §3.7 (subscribe), §3.12 (delivery/DLQ policy), §3.15.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .bus import EventBus
from .dedup import EventDeduplicator
from .metrics import record_consumed
from .router import EventHandler, EventRouter

DEFAULT_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0)
DEFAULT_MAX_RETRIES = 3


class Consumer:
    """Polls an EventBus stream via a consumer group and dispatches events.

    Usage:
        consumer = Consumer(redis_client, bus, group="main-group", consumer_name="worker-1")
        consumer.subscribe("call.started", on_call_started)
        consumer.poll_once()          # one bounded pass, for tests/step-driven use
        consumer.start(max_iterations=1)   # or run until stop() in a background thread/task
    """

    def __init__(
        self,
        redis: Any,
        bus: EventBus,
        group: str,
        consumer_name: str,
        dedup: EventDeduplicator | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        """
        Args:
            redis: A Redis-compatible client (``redis.Redis`` or ``FakeRedisClient``).
            bus: The EventBus this consumer reads from.
            group: Consumer group name (parallel consumption unit).
            consumer_name: This consumer's identity within the group.
            dedup: Optional EventDeduplicator; a stream-scoped one is created
                   if not supplied.
            max_retries: Processing attempts before DLQ routing.
            backoff_seconds: Delay before each retry (index = attempt - 1;
                             the last value is reused if attempts exceed
                             the tuple length).
            sleep_fn: Injectable sleep (tests pass a no-op to avoid waiting
                      out real backoff delays).
        """
        self._redis = redis
        self._bus = bus
        self._group = group
        self._consumer_name = consumer_name
        self._router = EventRouter()
        self._dedup = dedup or EventDeduplicator(redis, stream=bus.stream_name)
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._sleep_fn = sleep_fn
        self._running = False
        bus.ensure_consumer_group(group)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Register ``handler`` for events of ``event_type``."""
        self._router.register(event_type, handler)

    def poll_once(self, count: int = 10) -> int:
        """Read up to ``count`` new entries once and process them.

        Returns:
            The number of entries processed (successfully or DLQ-routed).
        """
        response = self._redis.xreadgroup(
            self._group,
            self._consumer_name,
            {self._bus.stream_name: ">"},
            count=count,
        )
        processed = 0
        for _stream_name, entries in response:
            for entry_id_raw, fields in entries:
                entry_id = entry_id_raw.decode() if isinstance(entry_id_raw, bytes) else entry_id_raw
                self._process_entry(entry_id, fields)
                processed += 1
        return processed

    def start(self, max_iterations: int | None = None, poll_count: int = 10, idle_sleep_s: float = 0.0) -> None:
        """Begin the polling loop.

        Args:
            max_iterations: Bound the loop for tests/bounded runs; None runs
                             until :meth:`stop` is called (production use —
                             the background worker owns its own thread/task).
            poll_count: Max entries read per XREADGROUP call.
            idle_sleep_s: Optional delay between empty polls to avoid a
                          busy-loop when the stream is quiet.
        """
        self._running = True
        iterations = 0
        while self._running:
            processed = self.poll_once(count=poll_count)
            iterations += 1
            if processed == 0 and idle_sleep_s > 0:
                self._sleep_fn(idle_sleep_s)
            if max_iterations is not None and iterations >= max_iterations:
                break

    def stop(self) -> None:
        """Signal the polling loop (:meth:`start`) to exit after its current pass."""
        self._running = False

    # ------------------------------------------------------------------
    # Internal delivery mechanics
    # ------------------------------------------------------------------

    def _process_entry(self, entry_id: str, fields: dict[Any, Any]) -> None:
        envelope = self._bus.decode_envelope(fields)

        if self._dedup.is_duplicate(envelope.event_id):
            self._ack(entry_id)
            return

        handlers = self._router.handlers_for(envelope.event_type)
        last_exc: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                for handler in handlers:
                    handler(envelope.payload)
                self._ack(entry_id)
                record_consumed(envelope.event_type, self._bus.stream_name, self._group)
                return
            except Exception as exc:  # handler errors drive retry/DLQ — must not propagate
                last_exc = exc
                if attempt < self._max_retries:
                    delay = self._backoff_seconds[min(attempt - 1, len(self._backoff_seconds) - 1)]
                    self._sleep_fn(delay)

        self._bus.route_to_dlq(entry_id, fields, reason=str(last_exc), attempt_count=self._max_retries)
        self._ack(entry_id)

    def _ack(self, entry_id: str) -> None:
        self._redis.xack(self._bus.stream_name, self._group, entry_id)
