"""TTLGuard — enforces mandatory TTL discipline on every Redis SET.

Redis is explicitly non-authoritative hot state (V3 Ch4 §4.2/4.4): every key
must carry a TTL so a lost or forgotten write self-heals instead of
accumulating forever. ``TTLGuard.set()`` is the only sanctioned way to write
a Redis string key in VoiceOS; a bare ``redis.set()`` call with no expiry is
a defect (RI-3 spirit — bounded resources — applied to Redis key space).

Architecture: V3 Ch4 §4.12 (TTL strategy); V6 Ch6.
"""

from __future__ import annotations

from typing import Any, Protocol


class MissingTTLError(Exception):
    """Raised when a Redis SET is attempted without a TTL.

    Any of ``ex``, ``px``, or ``exat`` satisfies the requirement. Omitting
    all three would create an eternal key, which V3 Ch4 forbids.
    """

    def __init__(self, key: str) -> None:
        super().__init__(f"Redis SET on key '{key}' is missing a TTL (ex/px/exat). All VoiceOS Redis keys must expire.")
        self.key = key


class _SupportsSet(Protocol):
    def set(
        self,
        key: str,
        value: Any,
        ex: int | None = ...,
        px: int | None = ...,
        exat: int | None = ...,
        nx: bool = ...,
    ) -> Any: ...


class TTLGuard:
    """Wraps a Redis client and rejects SETs that lack a TTL.

    Args:
        redis: A Redis-compatible client (``redis.Redis`` or ``FakeRedisClient``)
               exposing ``set(key, value, ex=None, px=None, exat=None, nx=False)``.

    Usage:
        guard = TTLGuard(redis_client)
        guard.set("voiceos:tenant-1:lock:call-1", b"owner-a", ex=30)   # OK
        guard.set("voiceos:tenant-1:lock:call-1", b"owner-a")         # raises MissingTTLError
    """

    def __init__(self, redis: _SupportsSet) -> None:
        self._redis = redis

    def set(
        self,
        key: str,
        value: bytes | str,
        ex: int | None = None,
        px: int | None = None,
        exat: int | None = None,
        nx: bool = False,
    ) -> Any:
        """Set ``key`` to ``value``, requiring at least one TTL form.

        Args:
            key: Redis key. Should follow ``namespace:tenant_id:resource:id``.
            value: Value to store.
            ex: Expiry in seconds.
            px: Expiry in milliseconds.
            exat: Expiry as a Unix timestamp (seconds).
            nx: If True, only set if the key does not already exist.

        Raises:
            MissingTTLError: If ex, px, and exat are all None.
        """
        if ex is None and px is None and exat is None:
            raise MissingTTLError(key)
        return self._redis.set(key, value, ex=ex, px=px, exat=exat, nx=nx)
