"""RedisClient — pooled connection to the hot-state Redis tier.

Redis is explicitly non-authoritative (V3 Ch4 §4.3/4.4): a coordination and
cache layer, never the system of record. This wrapper owns connection
pooling and a health check, and is the single place VoiceOS services obtain
a Redis handle — either a real ``redis.Redis`` (production) or a
``FakeRedisClient`` (unit tests, injected directly, bypassing ``from_url``).

Architecture: V3 Ch4 §4.7 (HotState public interface), §4.13 (config).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from src.libs.circuit_breaker.breaker import CircuitBreaker

T = TypeVar("T")


class RedisClient:
    """Pooled Redis connection wrapper with a health check.

    Wraps ``redis.Redis`` (created via a connection pool from a URL) or an
    injected test double. Downstream libraries (EventBus, DistributedLock,
    RateLimiter, TTLGuard) receive the raw client via :attr:`raw` and are
    agnostic to whether it is real or fake — matching the existing
    ``WorkingMemoryStore`` pattern (Sprint-010).

    Usage:
        client = RedisClient.from_url("redis://localhost:6379/0")
        client.raw.get("some:key")
        client.health_check()  # -> True/False, never raises

        # Unit tests:
        client = RedisClient(FakeRedisClient())
    """

    def __init__(self, redis: Any, breaker: CircuitBreaker | None = None) -> None:
        """
        Args:
            redis: A Redis-compatible client (``redis.Redis`` or ``FakeRedisClient``).
            breaker: Optional CircuitBreaker guarding Redis operations
                (Sprint-016, V3 Ch14 §14.2). None (default) preserves
                pre-Sprint-016 behavior.
        """
        self._redis = redis
        self._breaker = breaker

    @classmethod
    def from_url(
        cls,
        url: str,
        max_connections: int = 50,
        socket_timeout_s: float = 2.0,
        socket_connect_timeout_s: float = 2.0,
    ) -> RedisClient:
        """Create a RedisClient backed by a real pooled ``redis.Redis`` connection.

        Args:
            url: Redis connection URL, e.g. ``redis://localhost:6379/0``.
            max_connections: Maximum pool size (bounded — RI-3 spirit).
            socket_timeout_s: Per-command socket timeout.
            socket_connect_timeout_s: Connection establishment timeout.
        """
        import redis as redis_lib

        pool = redis_lib.ConnectionPool.from_url(
            url,
            max_connections=max_connections,
            socket_timeout=socket_timeout_s,
            socket_connect_timeout=socket_connect_timeout_s,
            decode_responses=False,
            # RESP2: broad compatibility with Redis versions predating the
            # RESP3 HELLO handshake (Redis < 6). See tests/fixtures/redis.py
            # TestRedis for the same rationale.
            protocol=2,
        )
        return cls(redis_lib.Redis(connection_pool=pool))

    @property
    def raw(self) -> Any:
        """The underlying Redis-compatible client (real or fake)."""
        return self._redis

    def health_check(self) -> bool:
        """Ping Redis. Returns False (never raises) on any connectivity failure."""
        try:
            return bool(self.call_guarded(self._redis.ping))
        except Exception:
            return False

    def call_guarded(self, fn: Callable[..., T], *args: object, **kwargs: object) -> T:
        """Invoke a Redis operation, routed through the optional CircuitBreaker.

        Sprint-016 (V3 Ch14 §14.2): callers that want fail-fast protection on
        the Redis dependency call through here instead of ``self.raw`` directly.
        With no breaker configured, this is a plain passthrough call.
        """
        if self._breaker is not None:
            return self._breaker.call_sync(fn, *args, **kwargs)
        return fn(*args, **kwargs)

    def close(self) -> None:
        """Close the underlying connection/pool, if supported."""
        close = getattr(self._redis, "close", None)
        if callable(close):
            close()
