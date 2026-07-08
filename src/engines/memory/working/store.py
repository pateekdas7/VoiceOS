"""WorkingMemoryStore — Redis-backed per-call state store.

The store serializes WorkingMemory as JSON and stores it in Redis with
a 4-hour TTL. The redis client is injected into the constructor so unit
tests can use FakeRedisClient and integration tests can use a real connection.

Interface:
  get(call_id)           → WorkingMemory (creates empty if not found)
  update(call_id, delta) → None (read-modify-write, resets TTL)
  clear(call_id)         → None

Architecture: V2 Ch11 (Working Memory).
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from prometheus_client import Counter, Histogram

from src.libs.redis_client.ttl_guard import TTLGuard

from .schema import WorkingMemory, WorkingMemoryDelta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

_WM_OPS = Counter(
    "working_memory_operations_total",
    "Total working memory operations",
    ["operation"],
)

_WM_LATENCY = Histogram(
    "working_memory_operation_latency_seconds",
    "Working memory operation latency in seconds",
    buckets=[0.001, 0.005, 0.010, 0.025, 0.050, 0.100],
)


class WorkingMemoryStore:
    """Redis-backed per-call working memory store.

    Architecture: V2 Ch11.
    TTL: WORKING_MEMORY_TTL seconds (4 hours = 14400s).

    The redis client must expose: ping(), set(key, value, ex=None),
    get(key), delete(*keys) — matching both redis.Redis and FakeRedisClient.
    """

    WORKING_MEMORY_TTL: ClassVar[int] = 14400  # 4 hours

    _KEY_PREFIX: ClassVar[str] = "wm:"

    def __init__(self, redis: Any) -> None:
        """
        Args:
            redis: A Redis client (redis.Redis or FakeRedisClient for tests).
        """
        self._redis = redis
        self._ttl_guard = TTLGuard(redis)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get(self, call_id: str) -> WorkingMemory:
        """Retrieve working memory for a call.

        Returns a fresh empty WorkingMemory if no entry exists in Redis.
        The returned object is immutable (Pydantic frozen model).

        Args:
            call_id: The call session identifier.
        """
        key = self._key(call_id)
        raw: bytes | None = self._redis.get(key)
        if raw is None:
            logger.debug("Working memory miss — returning empty", extra={"call_id": call_id})
            return WorkingMemory(call_id=call_id)

        data: dict[str, Any] = json.loads(raw.decode("utf-8"))
        _WM_OPS.labels(operation="get").inc()
        return WorkingMemory.model_validate(data)

    def update(self, call_id: str, delta: WorkingMemoryDelta) -> None:
        """Apply a partial update to working memory.

        Read-modify-write: fetches the current state, merges the delta,
        and writes back with the full TTL reset.

        Args:
            call_id: The call session identifier.
            delta: Fields to update; None-valued fields are unchanged.
        """
        current = self.get(call_id)

        merged_data = current.model_dump()

        delta_data = delta.model_dump(exclude_none=True)
        merged_data.update(delta_data)

        updated = WorkingMemory.model_validate(merged_data)
        self._write(call_id, updated)
        _WM_OPS.labels(operation="update").inc()

        logger.debug(
            "Working memory updated",
            extra={"call_id": call_id, "fields": list(delta_data.keys())},
        )

    def clear(self, call_id: str) -> None:
        """Delete working memory for a call.

        Args:
            call_id: The call session identifier.
        """
        key = self._key(call_id)
        self._redis.delete(key)
        _WM_OPS.labels(operation="clear").inc()
        logger.debug("Working memory cleared", extra={"call_id": call_id})

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _write(self, call_id: str, memory: WorkingMemory) -> None:
        key = self._key(call_id)
        payload = json.dumps(memory.model_dump())
        self._ttl_guard.set(key, payload.encode("utf-8"), ex=self.WORKING_MEMORY_TTL)

    @staticmethod
    def _key(call_id: str) -> str:
        return f"{WorkingMemoryStore._KEY_PREFIX}{call_id}"
