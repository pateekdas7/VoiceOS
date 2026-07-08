"""Unit tests: DistributedLock (fencing tokens, V3 Ch4 §4.11/4.12).

Architecture: V3 Ch4; V1 Appendix E RI-2; Sprint-013.
"""

from __future__ import annotations

from src.libs.redis_client.lock import DistributedLock, LockToken
from tests.fixtures.redis import FakeRedisClient


class TestDistributedLockAcquire:
    def test_acquire_returns_lock_token_with_fencing_token(self, fake_redis: FakeRedisClient) -> None:
        lock = DistributedLock(fake_redis)
        token = lock.acquire("call:abc123", owner_id="worker-1")
        assert isinstance(token, LockToken)
        assert token.resource_id == "call:abc123"
        assert token.owner_id == "worker-1"
        assert token.fencing_token >= 1

    def test_fencing_token_increases_across_reacquisitions(self, fake_redis: FakeRedisClient) -> None:
        lock = DistributedLock(fake_redis)
        first = lock.acquire("call:abc123", owner_id="worker-1")
        assert first is not None
        lock.release(first)
        second = lock.acquire("call:abc123", owner_id="worker-2")
        assert second is not None
        assert second.fencing_token > first.fencing_token

    def test_acquire_fails_when_already_held(self, fake_redis: FakeRedisClient) -> None:
        lock = DistributedLock(fake_redis)
        first = lock.acquire("call:abc123", owner_id="worker-1")
        assert first is not None
        second = lock.acquire("call:abc123", owner_id="worker-2")
        assert second is None

    def test_acquire_sets_ttl_on_lock_key(self, fake_redis: FakeRedisClient) -> None:
        lock = DistributedLock(fake_redis, ttl_ms=5000)
        lock.acquire("call:abc123", owner_id="worker-1")
        assert fake_redis.ttl("voiceos:lock:call:abc123") == 5000


class TestDistributedLockRelease:
    def test_distributed_lock_wrong_owner_release(self, fake_redis: FakeRedisClient) -> None:
        """release() with the wrong owner_id does NOT release the lock (Sprint-013 AC)."""
        lock = DistributedLock(fake_redis)
        token = lock.acquire("call:abc123", owner_id="worker-1")
        assert token is not None

        forged_token = LockToken(resource_id="call:abc123", owner_id="worker-EVIL", fencing_token=token.fencing_token)
        released = lock.release(forged_token)

        assert released is False
        # The legitimate owner can still acquire/hold — lock was untouched.
        still_held = lock.acquire("call:abc123", owner_id="worker-2")
        assert still_held is None

    def test_release_by_correct_owner_succeeds(self, fake_redis: FakeRedisClient) -> None:
        lock = DistributedLock(fake_redis)
        token = lock.acquire("call:abc123", owner_id="worker-1")
        assert token is not None
        released = lock.release(token)
        assert released is True
        reacquired = lock.acquire("call:abc123", owner_id="worker-2")
        assert reacquired is not None

    def test_release_of_already_released_lock_returns_false(self, fake_redis: FakeRedisClient) -> None:
        lock = DistributedLock(fake_redis)
        token = lock.acquire("call:abc123", owner_id="worker-1")
        assert token is not None
        assert lock.release(token) is True
        assert lock.release(token) is False


class TestDistributedLockSplitBrain:
    def test_stale_owner_cannot_release_after_takeover(self, fake_redis: FakeRedisClient) -> None:
        """Simulates V3 Ch4 §4.11: A stalls past TTL, B takes over, A's stale release must fail."""
        lock = DistributedLock(fake_redis)
        token_a = lock.acquire("call:abc123", owner_id="worker-A")
        assert token_a is not None

        # Simulate A's lock expiring (TTL elapsed) by force-deleting the key,
        # then B acquires — the resource now belongs to B.
        fake_redis.delete("voiceos:lock:call:abc123")
        token_b = lock.acquire("call:abc123", owner_id="worker-B")
        assert token_b is not None
        assert token_b.fencing_token > token_a.fencing_token

        # A, unaware it was superseded, tries to release using its stale token.
        stale_release = lock.release(token_a)
        assert stale_release is False
        # B's ownership survives.
        assert lock.release(token_b) is True
