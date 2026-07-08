"""Integration tests: Redis connectivity, set/get, TTL, and cleanup.

Requires REDIS_URL environment variable. Skipped automatically when absent.

Run:
    REDIS_URL=redis://localhost:6379/0 \\
    pytest tests/integration/test_redis_connectivity.py -v

Architecture: V3 Ch4 (Redis architecture); V6 Ch9; DocSuite-08.
"""

from __future__ import annotations

from tests.fixtures.redis import TestRedis
from tests.integration.conftest import requires_redis


@requires_redis
class TestRedisConnectivity:
    """Redis connection, set/get, expiry, and teardown integration tests."""

    _KEY_PREFIX: str = "voiceos:test:sprint003:"

    def test_connect_and_ping(self) -> None:
        """Can connect to Redis and receive a PONG response."""
        with TestRedis() as r:
            result = r.client.ping()
            assert result is True

    def test_set_and_get_bytes(self) -> None:
        """Can write a bytes value and read it back unchanged."""
        key = self._KEY_PREFIX + "set_get"
        with TestRedis() as r:
            r.flush()
            r.client.set(key, b"hello-voiceos")
            value = r.client.get(key)
            assert value == b"hello-voiceos"

    def test_delete_key(self) -> None:
        """Deleting a key makes it unreadable."""
        key = self._KEY_PREFIX + "delete"
        with TestRedis() as r:
            r.flush()
            r.client.set(key, b"data")
            r.client.delete(key)
            assert r.client.get(key) is None

    def test_key_does_not_exist_returns_none(self) -> None:
        """GET on a non-existent key returns None."""
        with TestRedis() as r:
            r.flush()
            result = r.client.get(self._KEY_PREFIX + "nonexistent")
            assert result is None

    def test_set_with_expiry(self) -> None:
        """A key set with ex has a positive TTL immediately after setting."""
        key = self._KEY_PREFIX + "ttl"
        with TestRedis() as r:
            r.flush()
            r.client.set(key, b"temp", ex=3600)
            ttl = r.client.ttl(key)
            assert isinstance(ttl, int)
            assert ttl > 0

    def test_flush_clears_test_db(self) -> None:
        """flush() empties the test database (db=15)."""
        key = self._KEY_PREFIX + "flush_check"
        with TestRedis() as r:
            r.client.set(key, b"before_flush")
            r.flush()
            assert r.client.get(key) is None

    def test_namespace_key_format(self) -> None:
        """Keys follow the VoiceOS namespace:tenant_id:resource:id convention."""
        tenant_id = "tenant-abc"
        resource = "call-state"
        call_id = "call-xyz"
        key = f"voiceos:{tenant_id}:{resource}:{call_id}"
        with TestRedis() as r:
            r.flush()
            r.client.set(key, b"active")
            assert r.client.get(key) == b"active"

    def test_disconnect_is_idempotent(self) -> None:
        """Calling close() twice does not raise."""
        r = TestRedis()
        r.connect()
        r.close()
        r.close()
