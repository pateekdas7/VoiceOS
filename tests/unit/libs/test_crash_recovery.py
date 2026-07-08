"""Unit tests for RecoveryManager and the six per-failure-class strategies (V3 Ch7)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from src.libs.contracts.events.envelope import EventEnvelope
from src.libs.contracts.primitives import CallId, TenantId
from src.libs.recovery.outcome import RecoveryOutcome
from src.libs.recovery.recovery_manager import RecoveryManager
from src.libs.recovery.strategies.cpu_restart import CPURestartStrategy
from src.libs.recovery.strategies.db_outage import DBOutageStrategy
from src.libs.recovery.strategies.gpu_failure import GPUFailureStrategy
from src.libs.recovery.strategies.network_partition import NetworkPartitionStrategy
from src.libs.recovery.strategies.redis_outage import RedisOutageStrategy
from src.libs.recovery.strategies.twilio_disconnect import TwilioDisconnectStrategy
from src.libs.state.recovery_log import RecoveryLog
from src.libs.state.replay import EventTailReplay
from src.libs.state.snapshot import Snapshot
from tests.fixtures.fake_pg import FakeConnection, FakeCursor
from tests.fixtures.recoverable import FakeEventBus, FakeRecoverable


class TestCPURestartStrategy:
    def test_cpu_restart_recovery(self) -> None:
        """Required Sprint-015 test: CPURestartStrategy.recover() -> state matches pre-crash."""
        tenant_id = TenantId("tenant-a")
        call_id = CallId("call-1")

        pre_crash = FakeRecoverable(call_id, counter=7)
        snapshot = pre_crash.snapshot()  # freezes counter=7 at version 1

        tail = [
            (
                # Entry IDs start at "1" — the snapshot's last_event_offset is
                # "0" (no events applied yet), and offsets are compared by
                # exact string match, so starting tail IDs at "0" would
                # collide with the sentinel and skip a real new event.
                str(i + 1),
                EventEnvelope(
                    event_type="decision.made",
                    tenant_id=tenant_id,
                    correlation_id="corr",
                    trace_id="trace",
                    payload={"call_id": call_id},
                ),
            )
            for i in range(3)
        ]
        # 3 more turns happen on the live component after the snapshot, then it crashes.
        for _entry_id, envelope in tail:
            pre_crash.apply_event(envelope)

        row = (snapshot.version, json.dumps(snapshot.state), snapshot.last_event_offset, datetime.now(UTC))
        snapshot_store = Snapshot(FakeConnection(FakeCursor(fetchall_results=[[row]])))
        replay = EventTailReplay(FakeEventBus(tail))
        strategy = CPURestartStrategy(snapshot_store, replay)

        restarted = FakeRecoverable(call_id)  # fresh instance simulating the post-restart process
        outcome = strategy.recover(restarted, tenant_id, call_id)

        assert outcome.success is True
        assert outcome.detail["events_replayed"] == 3
        assert restarted.counter == pre_crash.counter  # identical to pre-crash state

    def test_cpu_restart_with_no_prior_snapshot(self) -> None:
        tenant_id = TenantId("tenant-a")
        call_id = CallId("call-1")
        snapshot_store = Snapshot(FakeConnection(FakeCursor(fetchall_results=[[]])))
        replay = EventTailReplay(FakeEventBus([]))
        strategy = CPURestartStrategy(snapshot_store, replay)

        outcome = strategy.recover(FakeRecoverable(call_id), tenant_id, call_id)

        assert outcome.success is True
        assert outcome.detail["snapshot_version"] == 0


class TestGPUFailureStrategy:
    async def test_halts_tts_and_delegates_to_failover(self) -> None:
        halted_calls: list[str] = []

        class _FakeTTSHalt:
            async def halt_current_synthesis(self, call_id: str) -> None:
                halted_calls.append(call_id)

        class _FakeGPUFailover:
            def handle_device_failure(self, failed_device_id: str) -> str:
                return f"drained {failed_device_id}"

        strategy = GPUFailureStrategy(_FakeGPUFailover(), _FakeTTSHalt())

        outcome = await strategy.recover(CallId("call-1"), "gpu-0")

        assert outcome.success is True
        assert halted_calls == ["call-1"]
        assert outcome.detail["failed_device_id"] == "gpu-0"

    async def test_works_without_tts_halt_port(self) -> None:
        class _FakeGPUFailover:
            def handle_device_failure(self, failed_device_id: str) -> str:
                return "ok"

        strategy = GPUFailureStrategy(_FakeGPUFailover())

        outcome = await strategy.recover(CallId("call-1"), "gpu-0")

        assert outcome.success is True


class TestRedisOutageStrategy:
    def test_recover_enters_degraded_mode(self) -> None:
        strategy = RedisOutageStrategy()
        assert strategy.is_degraded is False

        outcome = strategy.recover()

        assert outcome.success is True
        assert strategy.is_degraded is True

    def test_resolve_exits_degraded_mode(self) -> None:
        strategy = RedisOutageStrategy()
        strategy.recover()
        strategy.resolve()

        assert strategy.is_degraded is False


class TestDBOutageStrategy:
    def test_recover_halts_new_call_admission(self) -> None:
        strategy = DBOutageStrategy()
        assert strategy.accepting_new_calls is True

        outcome = strategy.recover()

        assert outcome.success is True
        assert strategy.accepting_new_calls is False

    def test_resolve_resumes_admission(self) -> None:
        strategy = DBOutageStrategy()
        strategy.recover()
        strategy.resolve()

        assert strategy.accepting_new_calls is True


class TestTwilioDisconnectStrategy:
    async def test_recovers_when_reconnect_succeeds_within_window(self) -> None:
        strategy = TwilioDisconnectStrategy(
            reconnect_timeout_seconds=1.0,
            poll_interval_seconds=0.0,
            sleep_fn=lambda _s: _immediate(),
        )

        async def reconnect_check() -> bool:
            return True

        outcome = await strategy.recover(CallId("call-1"), reconnect_check)

        assert outcome.success is True
        assert outcome.detail["reconnected"] is True

    async def test_closes_session_when_reconnect_window_expires(self) -> None:
        strategy = TwilioDisconnectStrategy(
            reconnect_timeout_seconds=0.05,
            poll_interval_seconds=0.01,
            sleep_fn=lambda _s: _immediate(),
        )

        async def reconnect_check() -> bool:
            return False

        outcome = await strategy.recover(CallId("call-1"), reconnect_check)

        assert outcome.success is False
        assert outcome.detail["action"] == "session_closed"


async def _immediate() -> None:
    return None


class TestNetworkPartitionStrategy:
    def test_recover_enters_island_mode_and_drains_calls(self) -> None:
        strategy = NetworkPartitionStrategy()

        outcome = strategy.recover([CallId("call-1"), CallId("call-2")])

        assert outcome.success is True
        assert strategy.is_island_mode is True
        assert outcome.detail["drained_calls"] == ["call-1", "call-2"]

    def test_resolve_exits_island_mode(self) -> None:
        strategy = NetworkPartitionStrategy()
        strategy.recover([])
        strategy.resolve()

        assert strategy.is_island_mode is False


class TestRecoveryManagerDispatch:
    def test_dispatches_to_correct_strategy_per_failure_class(self) -> None:
        """AC: RecoveryManager correctly dispatches to the right strategy for each failure class."""
        manager = RecoveryManager(RecoveryLog(FakeConnection(FakeCursor())))
        strategies = {
            "cpu_restart": object(),
            "gpu_failure": object(),
            "redis_outage": object(),
            "db_outage": object(),
            "twilio_disconnect": object(),
            "network_partition": object(),
        }
        for failure_class, strategy in strategies.items():
            manager.register(failure_class, strategy)

        for failure_class, strategy in strategies.items():
            assert manager.strategy_for(failure_class) is strategy

    def test_unregistered_failure_class_raises(self) -> None:
        manager = RecoveryManager(RecoveryLog(FakeConnection(FakeCursor())))

        with pytest.raises(ValueError, match="No recovery strategy registered"):
            manager.strategy_for("unknown_failure")

    async def test_recover_records_success_outcome_to_recovery_log(self) -> None:
        cursor = FakeCursor()
        manager = RecoveryManager(RecoveryLog(FakeConnection(cursor)))
        manager.register("cpu_restart", _StubStrategy())

        async def recover_fn(_strategy: object) -> RecoveryOutcome:
            return RecoveryOutcome(success=True, detail={"events_replayed": 2})

        outcome = await manager.recover(TenantId("tenant-a"), CallId("call-1"), "cpu_restart", recover_fn)

        assert outcome.success is True
        sql, params = cursor.executed[0]
        assert "INSERT INTO recovery_log" in sql
        assert params[4] == "success"

    async def test_recover_records_failure_outcome_when_strategy_raises(self) -> None:
        cursor = FakeCursor()
        manager = RecoveryManager(RecoveryLog(FakeConnection(cursor)))
        manager.register("cpu_restart", _StubStrategy())

        async def recover_fn(_strategy: object) -> RecoveryOutcome:
            raise RuntimeError("boom")

        outcome = await manager.recover(TenantId("tenant-a"), CallId("call-1"), "cpu_restart", recover_fn)

        assert outcome.success is False
        _sql, params = cursor.executed[0]
        assert params[4] == "failure"


class _StubStrategy:
    """Minimal strategy stub exposing only ``strategy_name`` for RecoveryManager tests."""

    strategy_name = "cpu_restart"
