"""Unit tests for scripts/eventbus_recovery.py — TT-002 automatic recovery.

Uses FakeRedisClient (the same fixture EventBus/Consumer unit tests use) so
the detect→recreate→re-verify logic is exercised without a real Redis
connection.

Architecture: V3 Ch3 (EventBus); V3 Ch4 §4.13 (Redis persistence — TT-002).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from tests.fixtures.redis import FakeRedisClient

_SPEC = importlib.util.spec_from_file_location(
    "eventbus_recovery", Path(__file__).parent.parent.parent / "scripts" / "eventbus_recovery.py"
)
assert _SPEC is not None and _SPEC.loader is not None
eventbus_recovery = importlib.util.module_from_spec(_SPEC)
sys.modules["eventbus_recovery"] = eventbus_recovery
_SPEC.loader.exec_module(eventbus_recovery)


class TestConsumerGroupExists:
    def test_false_when_stream_does_not_exist(self) -> None:
        client = FakeRedisClient()
        assert eventbus_recovery.consumer_group_exists(client, "missing-stream", "main-group") is False

    def test_false_when_stream_exists_but_group_does_not(self) -> None:
        client = FakeRedisClient()
        client.xadd("voiceos-events", {"envelope": "{}"})
        assert eventbus_recovery.consumer_group_exists(client, "voiceos-events", "main-group") is False

    def test_true_when_group_present(self) -> None:
        client = FakeRedisClient()
        client.xgroup_create("voiceos-events", "main-group", id="0", mkstream=True)
        assert eventbus_recovery.consumer_group_exists(client, "voiceos-events", "main-group") is True


class TestRecover:
    def test_recreates_missing_group(self) -> None:
        client = FakeRedisClient()

        healthy = eventbus_recovery.recover(client, "voiceos-events", "main-group", "main-group-dlq")

        assert healthy is True
        assert eventbus_recovery.consumer_group_exists(client, "voiceos-events", "main-group") is True

    def test_idempotent_on_already_present_group(self) -> None:
        """Sprint calling this repeatedly (e.g. from healthcheck.sh) must never raise or duplicate."""
        client = FakeRedisClient()

        first = eventbus_recovery.recover(client, "voiceos-events", "main-group", "main-group-dlq")
        second = eventbus_recovery.recover(client, "voiceos-events", "main-group", "main-group-dlq")

        assert first is True
        assert second is True

    def test_precreates_dlq_stream_with_group(self) -> None:
        client = FakeRedisClient()

        eventbus_recovery.recover(client, "voiceos-events", "main-group", "main-group-dlq")

        assert eventbus_recovery.consumer_group_exists(client, "dlq:voiceos-events", "main-group-dlq") is True

    def test_survives_simulated_redis_restart_data_loss(self) -> None:
        """The exact TT-002 scenario: group exists, Redis loses all data, recovery recreates it."""
        client = FakeRedisClient()
        eventbus_recovery.recover(client, "voiceos-events", "main-group", "main-group-dlq")
        assert eventbus_recovery.consumer_group_exists(client, "voiceos-events", "main-group") is True

        # Simulate a Redis restart with no persistence: the in-memory dataset is gone.
        client = FakeRedisClient()
        assert eventbus_recovery.consumer_group_exists(client, "voiceos-events", "main-group") is False

        healthy = eventbus_recovery.recover(client, "voiceos-events", "main-group", "main-group-dlq")
        assert healthy is True
