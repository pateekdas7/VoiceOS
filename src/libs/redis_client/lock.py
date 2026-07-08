"""DistributedLock — Redlock-style single-owner lock with fencing tokens.

Implements V3 Ch4 §4.12's fencing-token algorithm: ``SET key owner NX PX ttl``
for atomic acquire, plus a monotonically increasing fencing token so a
stalled former owner cannot corrupt state after another owner takes over
(the classic Redlock split-brain fix — see V3 Ch4 §4.11 sequence diagram).

Every successful acquire is self-verified against RI-2 (single-writer state)
so a coverage test can confirm the invariant is exercised on the hot path.

Architecture: V3 Ch4 §4.7, §4.9, §4.11, §4.12; V1 Appendix E RI-2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.libs.invariants.guards import assert_ri2_single_writer

from .ttl_guard import TTLGuard

_KEY_PREFIX = "voiceos:lock:"
_FENCING_KEY_PREFIX = "voiceos:lock:fencing:"

# Atomically verify ownership before deleting — prevents a stale/former owner
# (e.g. resumed after a GC pause past its own TTL) from releasing a lock that
# has since been re-acquired by someone else.
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


def _fake_release_handler(client: Any, keys: list[str], args: list[str]) -> int:
    """FakeRedisClient emulation of ``_RELEASE_SCRIPT`` (compare-then-delete)."""
    key = keys[0]
    owner = args[0]
    current = client.get(key)
    if current is not None and current.decode() == owner:
        client.delete(key)
        return 1
    return 0


@dataclass(frozen=True)
class LockToken:
    """A held distributed lock, including its fencing token.

    Callers must present ``fencing_token`` to :func:`assert_ri2_single_writer`
    (or an equivalent compare-and-check on the target store) before writing
    the guarded resource, so a superseded owner's stale write is rejected.
    """

    resource_id: str
    owner_id: str
    fencing_token: int


class DistributedLock:
    """Redis-backed distributed lock with fencing tokens (V3 Ch4).

    Usage:
        lock = DistributedLock(redis_client)
        token = lock.acquire("call:abc123", owner_id="worker-7")
        if token is not None:
            try:
                ...  # guarded work, present token.fencing_token to writers
            finally:
                lock.release(token)
    """

    DEFAULT_TTL_MS = 30_000
    """Default lock hold duration (V3 Ch4 §4.13: lock_ms: 5000 is the
    architecture's baseline; VoiceOS uses a longer default here for
    call-scoped locks and lets callers override per resource)."""

    def __init__(self, redis: Any, ttl_ms: int = DEFAULT_TTL_MS) -> None:
        """
        Args:
            redis: A Redis-compatible client (``redis.Redis`` or ``FakeRedisClient``).
            ttl_ms: Lock hold duration in milliseconds before automatic expiry.
        """
        self._redis = redis
        self._ttl_guard = TTLGuard(redis)
        self._ttl_ms = ttl_ms
        register = getattr(redis, "register_recognized_script", None)
        if callable(register):
            register(_RELEASE_SCRIPT, _fake_release_handler)

    def acquire(self, resource_id: str, owner_id: str) -> LockToken | None:
        """Attempt to acquire the lock for ``resource_id``.

        Args:
            resource_id: The resource being guarded (e.g. ``'call:abc123'``).
            owner_id: Identifier of the requesting owner (e.g. worker/node id).

        Returns:
            A LockToken with a fresh fencing token if acquired, else None.
        """
        lock_key = self._lock_key(resource_id)
        acquired = self._ttl_guard.set(lock_key, owner_id, px=self._ttl_ms, nx=True)
        if not acquired:
            return None

        fencing_key = self._fencing_key(resource_id)
        fencing_token = int(self._redis.incr(fencing_key))
        # The fencing counter must outlive many individual lock holds so
        # tokens stay monotonic across re-acquisitions; TTL still applies
        # (V3 Ch4 TTL discipline) with a generous multiple of the lock TTL.
        self._redis.expire(fencing_key, max(self._ttl_ms // 1000, 1) * 100)

        assert_ri2_single_writer(resource_id=resource_id, writer_id=owner_id, lock_token=str(fencing_token))

        return LockToken(resource_id=resource_id, owner_id=owner_id, fencing_token=fencing_token)

    def release(self, token: LockToken) -> bool:
        """Release the lock, only if ``token.owner_id`` still owns it.

        Uses an atomic compare-then-delete (Lua script on real Redis; the
        equivalent emulation on FakeRedisClient) so a stale owner's release
        call cannot remove a lock that another owner has since acquired.

        Args:
            token: The LockToken returned by a prior successful acquire().

        Returns:
            True if this call released the lock; False if the lock was
            already gone or held by a different owner.
        """
        lock_key = self._lock_key(token.resource_id)
        released = self._redis.eval(_RELEASE_SCRIPT, 1, lock_key, token.owner_id)
        return bool(released)

    @staticmethod
    def _lock_key(resource_id: str) -> str:
        return f"{_KEY_PREFIX}{resource_id}"

    @staticmethod
    def _fencing_key(resource_id: str) -> str:
        return f"{_FENCING_KEY_PREFIX}{resource_id}"
