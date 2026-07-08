"""Unit tests for Snapshot, EventTailReplay, and RecoveryLog (V3 Ch6)."""

from __future__ import annotations

import json

from src.libs.contracts.events.envelope import EventEnvelope
from src.libs.contracts.primitives import CallId, TenantId
from src.libs.state.recovery_log import RecoveryAttempt, RecoveryLog
from src.libs.state.replay import EventTailReplay
from src.libs.state.snapshot import Snapshot
from tests.fixtures.fake_pg import FakeConnection, FakeCursor
from tests.fixtures.recoverable import FakeEventBus, FakeRecoverable


def _envelope(call_id: str, tenant_id: str = "tenant-a") -> EventEnvelope:
    return EventEnvelope(
        event_type="decision.made",
        tenant_id=TenantId(tenant_id),
        correlation_id="corr-1",
        trace_id="trace-1",
        payload={"call_id": call_id},
    )


class TestSnapshotRoundtrip:
    def test_snapshot_roundtrip(self) -> None:
        """Required Sprint-015 test: take snapshot -> restore -> state identical."""
        call_id = CallId("call-1")
        original = FakeRecoverable(call_id, counter=42)

        snap = original.snapshot()

        restored = FakeRecoverable(call_id)
        restored.restore(snap)

        assert restored.counter == original.counter == 42

    def test_snapshot_persists_via_repository(self) -> None:
        """Snapshot.take_snapshot() INSERTs into the snapshots table, tenant-scoped."""
        tenant_id = TenantId("tenant-a")
        call_id = CallId("call-1")
        component = FakeRecoverable(call_id, counter=5)

        cursor = FakeCursor()
        store = Snapshot(FakeConnection(cursor))

        returned = store.take_snapshot(component, tenant_id, call_id)

        assert returned.state == {"counter": 5}
        sql, params = cursor.executed[0]
        assert "INSERT INTO snapshots" in sql
        assert params[0] == tenant_id
        assert params[1] == call_id

    def test_load_latest_snapshot_returns_none_when_absent(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        store = Snapshot(FakeConnection(cursor))

        assert store.load_latest_snapshot(TenantId("tenant-a"), CallId("call-1")) is None

    def test_load_latest_snapshot_orders_by_version_desc(self) -> None:
        tenant_id = TenantId("tenant-a")
        call_id = CallId("call-1")
        row = (3, json.dumps({"counter": 9}), "2", "2026-07-04T00:00:00+00:00")
        cursor = FakeCursor(fetchall_results=[[row]])
        store = Snapshot(FakeConnection(cursor))

        loaded = store.load_latest_snapshot(tenant_id, call_id)

        assert loaded is not None
        assert loaded.version == 3
        assert loaded.state == {"counter": 9}
        sql, _params = cursor.executed[0]
        assert "ORDER BY version DESC" in sql
        assert "LIMIT" in sql


class TestEventTailReplay:
    def test_replay_restores_state(self) -> None:
        """Required Sprint-015 test: snapshot + 5 events -> replay -> correct final state."""
        call_id = CallId("call-1")

        live = FakeRecoverable(call_id, counter=10)
        snapshot = live.snapshot()  # version=1, counter=10, last_event_offset="0"

        tail = [(f"{i}-0", _envelope(call_id)) for i in range(1, 6)]
        replay = EventTailReplay(FakeEventBus(tail))

        restarted = FakeRecoverable(call_id)
        events_replayed = replay.replay_from_snapshot(call_id, restarted, snapshot)

        assert events_replayed == 5
        assert restarted.counter == 10 + 5

    def test_replay_skips_events_from_other_calls(self) -> None:
        call_id = CallId("call-1")
        other_call_id = CallId("call-2")

        tail = [
            ("1-0", _envelope(call_id)),
            ("2-0", _envelope(other_call_id)),
            ("3-0", _envelope(call_id)),
        ]
        replay = EventTailReplay(FakeEventBus(tail))
        component = FakeRecoverable(call_id)

        events_replayed = replay.replay_from_snapshot(call_id, component, snapshot=None)

        assert events_replayed == 2

    def test_replay_excludes_snapshot_offset_entry(self) -> None:
        """The entry the snapshot was taken at must not be re-applied (exclusive resume)."""
        call_id = CallId("call-1")
        component = FakeRecoverable(call_id, counter=1)
        component.apply_event(_envelope(call_id))  # applied_event_ids = ["<uuid>"]
        snapshot = component.snapshot()  # last_event_offset = "1"

        tail = [("1", _envelope(call_id)), ("2", _envelope(call_id))]
        replay = EventTailReplay(FakeEventBus(tail))

        restarted = FakeRecoverable(call_id)
        events_replayed = replay.replay_from_snapshot(call_id, restarted, snapshot)

        assert events_replayed == 1  # entry "1" skipped, only "2" applied

    def test_replay_from_none_snapshot_starts_from_beginning(self) -> None:
        call_id = CallId("call-1")
        tail = [(f"{i}", _envelope(call_id)) for i in range(3)]
        replay = EventTailReplay(FakeEventBus(tail))
        component = FakeRecoverable(call_id)

        events_replayed = replay.replay_from_snapshot(call_id, component, snapshot=None)

        assert events_replayed == 3
        assert component.counter == 3


class TestRecoveryLog:
    def test_record_inserts_recovery_attempt(self) -> None:
        from datetime import UTC, datetime

        cursor = FakeCursor()
        log = RecoveryLog(FakeConnection(cursor))
        attempt = RecoveryAttempt(
            call_id=CallId("call-1"),
            failure_class="cpu_restart",
            strategy_name="cpu_restart",
            outcome="success",
            detail={"events_replayed": 3},
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            duration_ms=120,
        )

        log.record(TenantId("tenant-a"), attempt)

        sql, params = cursor.executed[0]
        assert "INSERT INTO recovery_log" in sql
        assert params[0] == "tenant-a"
        assert params[4] == "success"

    def test_last_outcome_returns_most_recent(self) -> None:
        cursor = FakeCursor(fetchall_results=[[("failure",)]])
        log = RecoveryLog(FakeConnection(cursor))

        outcome = log.last_outcome(TenantId("tenant-a"), CallId("call-1"))

        assert outcome == "failure"

    def test_last_outcome_returns_none_when_absent(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        log = RecoveryLog(FakeConnection(cursor))

        assert log.last_outcome(TenantId("tenant-a"), CallId("call-1")) is None
