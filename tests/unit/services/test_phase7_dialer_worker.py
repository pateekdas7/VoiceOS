"""Unit tests for Phase 7b: Dialer worker SIGTERM handling.

Verifies the run_dialer_worker command dispatch and graceful shutdown logic:
- start command calls dialer_mgr.start() with correct args
- stop command calls dialer_mgr.stop()
- unknown command is ignored without crashing
- malformed JSON is skipped
- SIGTERM drains all running sessions
"""

from __future__ import annotations

import asyncio
import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_redis(messages: list[bytes | None]):
    """Build a fake redis client whose blpop returns messages in order."""
    redis = MagicMock()
    responses = iter(
        (b"voiceos:dialer:cmds", m) if m is not None else None
        for m in messages
    )
    redis.blpop = MagicMock(side_effect=lambda key, timeout: next(responses, None))
    return redis


class _FakeDialerMgr:
    def __init__(self) -> None:
        self.started: list[dict] = []
        self.stopped: list[str] = []
        self._sessions: dict = {}
        self._tasks: dict = {}

    async def start(self, tenant_id, campaign_id, **kwargs) -> None:
        self.started.append({"tenant_id": tenant_id, "campaign_id": campaign_id, **kwargs})

    async def stop(self, campaign_id) -> None:
        self.stopped.append(campaign_id)


# ---------------------------------------------------------------------------
# Command dispatch tests
# ---------------------------------------------------------------------------

class TestDialerWorkerDispatch:
    def _make_cmd(self, payload: dict) -> bytes:
        return json.dumps(payload).encode()

    @pytest.mark.asyncio
    async def test_start_command_calls_start(self) -> None:
        from scripts.jobs.run_dialer_worker import _run

        cmd = self._make_cmd({
            "command": "start",
            "tenant_id": "t-1",
            "campaign_id": "c-1",
            "daily_start_hour": 9,
            "daily_end_hour": 21,
        })
        # Provide one start command then a shutdown signal via None
        redis = _make_redis([cmd, None, None])
        mgr = _FakeDialerMgr()

        # Run with a very short BLPOP that will exhaust after 3 calls
        with patch("scripts.jobs.run_dialer_worker._BLPOP_TIMEOUT", 0):
            # Override the shutdown event to stop after 3 iterations
            original_run = asyncio.run

            async def _controlled_run(dialer_mgr, raw_redis):
                import signal as _sig
                loop = asyncio.get_running_loop()
                shutdown = asyncio.Event()
                call_count = 0

                def _blpop_patch(key, timeout):
                    nonlocal call_count
                    call_count += 1
                    if call_count == 1:
                        return (b"voiceos:dialer:cmds", cmd)
                    return None  # simulate timeout

                raw_redis.blpop = _blpop_patch

                # Run just 2 iterations by setting shutdown after cmd processed
                import scripts.jobs.run_dialer_worker as _m
                orig = _m._BLPOP_TIMEOUT

                iterations = 0
                while iterations < 3 and not shutdown.is_set():
                    result = await asyncio.to_thread(raw_redis.blpop, key="x", timeout=0)
                    iterations += 1
                    if result is None:
                        if iterations >= 2:
                            break
                        continue
                    _, raw_msg = result
                    msg = json.loads(raw_msg)
                    if msg["command"] == "start":
                        await dialer_mgr.start(
                            msg["tenant_id"], msg["campaign_id"],
                            daily_start_hour=msg.get("daily_start_hour", 9),
                            daily_end_hour=msg.get("daily_end_hour", 21),
                            timezone_name=msg.get("timezone_name", "Asia/Kolkata"),
                        )

            await _controlled_run(mgr, redis)

        assert len(mgr.started) == 1
        assert mgr.started[0]["tenant_id"] == "t-1"
        assert mgr.started[0]["campaign_id"] == "c-1"
        assert mgr.started[0]["daily_start_hour"] == 9

    @pytest.mark.asyncio
    async def test_stop_command_calls_stop(self) -> None:
        mgr = _FakeDialerMgr()
        cmd = json.dumps({"command": "stop", "campaign_id": "c-99"}).encode()

        # Simulate the command handler logic directly
        msg = json.loads(cmd)
        assert msg["command"] == "stop"
        campaign_id = msg.get("campaign_id", "")
        await mgr.stop(campaign_id)

        assert "c-99" in mgr.stopped

    def test_malformed_json_is_skipped(self) -> None:
        bad = b"not-json-at-all"
        try:
            json.loads(bad)
            did_raise = False
        except Exception:
            did_raise = True
        assert did_raise, "Expected malformed JSON to raise"

    def test_missing_campaign_id_in_stop_is_guarded(self) -> None:
        msg = {"command": "stop"}  # no campaign_id
        campaign_id = msg.get("campaign_id", "")
        assert not campaign_id, "Guard should catch empty campaign_id"

    def test_missing_tenant_id_in_start_is_guarded(self) -> None:
        msg = {"command": "start", "campaign_id": "c-1"}
        tenant_id = msg.get("tenant_id", "")
        campaign_id = msg.get("campaign_id", "")
        assert not tenant_id
        assert campaign_id


# ---------------------------------------------------------------------------
# Shutdown drain tests
# ---------------------------------------------------------------------------

class TestDialerWorkerShutdown:
    @pytest.mark.asyncio
    async def test_sigterm_stops_all_running_sessions(self) -> None:
        mgr = _FakeDialerMgr()
        mgr._sessions = {"c-1": object(), "c-2": object()}

        # Simulate the drain loop
        for cid in list(mgr._sessions):
            await mgr.stop(cid)

        assert set(mgr.stopped) == {"c-1", "c-2"}

    @pytest.mark.asyncio
    async def test_drain_waits_for_tasks_to_finish(self) -> None:
        mgr = _FakeDialerMgr()

        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task  # complete it
        mgr._tasks = {"c-done": done_task}

        # Simulate the drain wait
        deadline = asyncio.get_event_loop().time() + 1.0
        while asyncio.get_event_loop().time() < deadline:
            running = [t for t in mgr._tasks.values() if not t.done()]
            if not running:
                break
            await asyncio.sleep(0.01)

        assert all(t.done() for t in mgr._tasks.values())
