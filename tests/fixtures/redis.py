"""Redis test fixtures for VoiceOS.

FakeRedisClient  — in-memory, no-I/O Redis stub for unit tests.
TestRedis        — wraps a real Redis connection for integration tests.

Architecture: V6 Ch9 (Testing Standards); V3 Ch4 (Redis architecture);
              V3 Ch3 (Event Bus — Streams emulation); DocSuite-08.
"""

from __future__ import annotations

import itertools
import os
import time
from typing import Any

# ---------------------------------------------------------------------------
# Environment config
# ---------------------------------------------------------------------------

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# ---------------------------------------------------------------------------
# FakeRedisClient — for unit tests (no I/O)
# ---------------------------------------------------------------------------


class _FakeStream:
    """In-memory Redis Streams emulation for a single stream key.

    Tracks entries in insertion order plus, per consumer group, a
    last-delivered cursor and a pending-entries list (PEL) — enough fidelity
    to unit-test EventBus publish/consume/ack/retry/DLQ logic without a
    real Redis Streams backend.
    """

    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[bytes, bytes]]] = []
        self._by_id: dict[str, dict[bytes, bytes]] = {}
        self._seq = itertools.count(0)
        # group name -> {"cursor": int (index into entries), "pending": {entry_id: [consumer, delivery_count]}}
        self.groups: dict[str, dict[str, Any]] = {}

    def add(self, fields: dict[str, Any]) -> str:
        entry_id = f"{int(time.time() * 1000)}-{next(self._seq)}"
        encoded = {
            (k.encode() if isinstance(k, str) else k): (v.encode() if isinstance(v, str) else v)
            for k, v in fields.items()
        }
        self.entries.append((entry_id, encoded))
        self._by_id[entry_id] = encoded
        return entry_id

    def ensure_group(self, group: str) -> None:
        if group not in self.groups:
            self.groups[group] = {"cursor": len(self.entries), "pending": {}}


class FakeRedisClient:
    """In-memory Redis-compatible stub for unit tests.

    Implements the command surface used by VoiceOS services:
    ping, set (with ex/px/nx), get, delete, exists, expire, incr, ttl,
    flushdb, sorted sets (zadd/zremrangebyscore/zcard/zrange), a small
    recognized-script eval() for lock/rate-limiter Lua scripts, and a
    Streams subset (xadd/xlen/xgroup_create/xreadgroup/xack/xpending/xrange)
    for EventBus unit tests.

    Not thread-safe; not suitable for integration tests. Use TestRedis there.
    Architecture: V3 Ch4 keys follow namespace:tenant_id:resource:id format.
    """

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self._ttls: dict[str, int | None] = {}
        self._zsets: dict[str, dict[bytes, float]] = {}
        self._streams: dict[str, _FakeStream] = {}
        self._scripts: dict[str, Any] = {}

    def ping(self) -> bool:
        return True

    def set(
        self,
        key: str,
        value: bytes | str,
        ex: int | None = None,
        px: int | None = None,
        exat: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        """Set key → value. Optional TTL (ex/px/exat) and NX (only-if-absent)."""
        if nx and key in self._store:
            return None
        self._store[key] = value if isinstance(value, bytes) else value.encode()
        self._ttls[key] = ex if ex is not None else px if px is not None else exat
        return True

    def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    def delete(self, *keys: str) -> int:
        """Delete one or more keys. Returns the count of deleted keys."""
        deleted = 0
        for key in keys:
            if key in self._store:
                del self._store[key]
                self._ttls.pop(key, None)
                deleted += 1
        return deleted

    def exists(self, *keys: str) -> int:
        """Return the number of given keys that exist."""
        return sum(1 for k in keys if k in self._store)

    def expire(self, key: str, seconds: int) -> bool:
        """Set a TTL on an existing key. Returns True if key exists."""
        if key in self._store:
            self._ttls[key] = seconds
            return True
        return False

    def ttl(self, key: str) -> int:
        """Return the TTL in seconds; -1 if no TTL set, -2 if key absent."""
        if key not in self._store:
            return -2
        value = self._ttls.get(key)
        return value if value is not None else -1

    def incr(self, key: str) -> int:
        """Atomically increment (and create if absent) an integer key. No TTL by design — callers must expire() separately."""
        current = int(self._store.get(key, b"0"))
        current += 1
        self._store[key] = str(current).encode()
        return current

    def flushdb(self) -> bool:
        """Remove all keys from the store."""
        self._store.clear()
        self._ttls.clear()
        self._zsets.clear()
        self._streams.clear()
        return True

    # ------------------------------------------------------------------
    # Sorted sets (RateLimiter sliding window)
    # ------------------------------------------------------------------

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        zset = self._zsets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            member_b = member.encode() if isinstance(member, str) else member
            if member_b not in zset:
                added += 1
            zset[member_b] = score
        return added

    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        zset = self._zsets.get(key)
        if not zset:
            return 0
        to_remove = [m for m, s in zset.items() if min_score <= s <= max_score]
        for m in to_remove:
            del zset[m]
        return len(to_remove)

    def zcard(self, key: str) -> int:
        return len(self._zsets.get(key, {}))

    def zrange(self, key: str, start: int, end: int, withscores: bool = False) -> list[Any]:
        zset = self._zsets.get(key, {})
        ordered = sorted(zset.items(), key=lambda kv: kv[1])
        end_slice = None if end == -1 else end + 1
        window = ordered[start:end_slice]
        if withscores:
            return [(m, s) for m, s in window]
        return [m for m, _ in window]

    # ------------------------------------------------------------------
    # EVAL — recognized-script dispatch (no general Lua interpreter)
    # ------------------------------------------------------------------

    def register_recognized_script(self, script_text: str, handler: Any) -> None:
        """Register a Python callable to emulate a known Lua script's semantics.

        VoiceOS's lock and rate-limiter modules define their Lua scripts as
        module-level constants; this fake dispatches by exact script text so
        unit tests exercise the same call sites the real client would.
        """
        self._scripts[script_text] = handler

    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        handler = self._scripts.get(script)
        if handler is None:
            raise NotImplementedError(
                "FakeRedisClient.eval() received an unrecognized script. "
                "Register it via register_recognized_script() first."
            )
        keys = keys_and_args[:numkeys]
        args = keys_and_args[numkeys:]
        return handler(self, list(keys), list(args))

    # ------------------------------------------------------------------
    # Streams -- used by the event bus
    # ------------------------------------------------------------------

    def _stream(self, name: str) -> _FakeStream:
        return self._streams.setdefault(name, _FakeStream())

    def xadd(self, name: str, fields: dict[str, Any], id: str = "*") -> bytes:
        entry_id = self._stream(name).add(fields)
        return entry_id.encode()

    def xlen(self, name: str) -> int:
        if name not in self._streams:
            return 0
        return len(self._streams[name].entries)

    def xgroup_create(self, name: str, groupname: str, id: str = "$", mkstream: bool = False) -> bool:
        stream = self._stream(name)
        if groupname in stream.groups:
            raise ValueError(f"BUSYGROUP Consumer Group name already exists: {groupname}")
        stream.ensure_group(groupname)
        return True

    def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]]:
        result: list[tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]] = []
        for name, requested_id in streams.items():
            stream = self._stream(name)
            stream.ensure_group(groupname)
            group = stream.groups[groupname]
            if requested_id == ">":
                # Deliver new (never-delivered) entries.
                new_entries = stream.entries[group["cursor"] :]
                if count is not None:
                    new_entries = new_entries[:count]
                delivered: list[tuple[bytes, dict[bytes, bytes]]] = []
                for entry_id, fields in new_entries:
                    pending = group["pending"].setdefault(entry_id, [consumername, 0])
                    pending[1] += 1
                    delivered.append((entry_id.encode(), fields))
                group["cursor"] += len(new_entries)
                if delivered:
                    result.append((name.encode(), delivered))
            else:
                # Re-deliver this consumer's own pending entries (history read).
                delivered = [
                    (eid.encode(), dict(stream._by_id[eid]))
                    for eid, (owner, _cnt) in group["pending"].items()
                    if owner == consumername
                ]
                if delivered:
                    result.append((name.encode(), delivered))
        return result

    def xack(self, name: str, groupname: str, *ids: Any) -> int:
        if name not in self._streams:
            return 0
        group = self._streams[name].groups.get(groupname)
        if group is None:
            return 0
        acked = 0
        for entry_id in ids:
            key = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
            if key in group["pending"]:
                del group["pending"][key]
                acked += 1
        return acked

    def xpending(self, name: str, groupname: str) -> dict[str, Any]:
        if name not in self._streams or groupname not in self._streams[name].groups:
            return {"pending": 0, "min": None, "max": None, "consumers": []}
        pending = self._streams[name].groups[groupname]["pending"]
        if not pending:
            return {"pending": 0, "min": None, "max": None, "consumers": []}
        ids = sorted(pending.keys())
        return {
            "pending": len(pending),
            "min": ids[0].encode(),
            "max": ids[-1].encode(),
            "consumers": [{"name": c.encode(), "pending": 0} for c in {v[0] for v in pending.values()}],
        }

    def xpending_delivery_count(self, name: str, groupname: str, entry_id: str) -> int:
        """Non-standard helper (not a real Redis command): read a pending entry's delivery count.

        Used by Consumer's retry/backoff logic in the fake-backed unit tests,
        where XCLAIM's delivery-count semantics are not otherwise modeled.
        """
        stream = self._streams.get(name)
        if stream is None:
            return 0
        group = stream.groups.get(groupname)
        if group is None or entry_id not in group["pending"]:
            return 0
        return int(group["pending"][entry_id][1])

    def xrange(self, name: str, min: str = "-", max: str = "+", count: int | None = None) -> list[Any]:
        if name not in self._streams:
            return []
        entries = self._streams[name].entries
        if min != "-":
            entries = [e for e in entries if e[0] >= min]
        if max != "+":
            entries = [e for e in entries if e[0] <= max]
        if count is not None:
            entries = entries[:count]
        return [(eid.encode(), fields) for eid, fields in entries]

    def xinfo_groups(self, name: str) -> list[dict[str, Any]]:
        if name not in self._streams:
            return []
        return [
            {"name": g.encode(), "pending": len(state["pending"]), "consumers": 1}
            for g, state in self._streams[name].groups.items()
        ]


# ---------------------------------------------------------------------------
# TestRedis — for integration tests (real connection)
# ---------------------------------------------------------------------------


class TestRedis:
    """Real Redis test helper for integration tests.

    Connects to REDIS_URL (or the dsn argument) using database index 15
    (reserved for testing) to isolate test data from other databases.

    Usage (context manager — preferred):
        with TestRedis() as r:
            r.client.ping()
            r.client.set("key", b"value")

    Usage (manual):
        r = TestRedis()
        r.connect()
        r.client.ping()
        r.close()
    """

    def __init__(self, url: str | None = None) -> None:
        self._url: str = url if url is not None else REDIS_URL
        self._client: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open a connection to Redis.

        Raises:
            ImportError: If the redis package is not installed.
        """
        import redis as redis_lib

        self._client = redis_lib.Redis.from_url(
            self._url,
            db=15,
            decode_responses=False,
            # RESP2: some deployment targets (e.g. Redis 5.x/6.0 on the dev
            # box, before the CPU node's Sprint-013 Redis upgrade — see
            # CPU_NODE_STATE.md §7.1) predate the RESP3 HELLO handshake that
            # redis-py negotiates by default. Streams/EVAL/sorted-sets all
            # work identically under RESP2.
            protocol=2,
        )

    def close(self) -> None:
        """Close the Redis connection."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> TestRedis:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Properties and helpers
    # ------------------------------------------------------------------

    @property
    def client(self) -> Any:
        """The underlying redis.Redis client.

        Raises:
            RuntimeError: If connect() has not been called.
        """
        if self._client is None:
            raise RuntimeError("Not connected — call connect() or use as a context manager.")
        return self._client

    def flush(self) -> None:
        """Flush all keys from the test database (db=15)."""
        if self._client is not None:
            self._client.flushdb()
