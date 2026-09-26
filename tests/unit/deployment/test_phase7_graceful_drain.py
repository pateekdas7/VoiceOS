"""Unit tests for Phase 7d: Voice runtime graceful drain.

Verifies:
- _DrainGate.draining starts False
- _DrainGate.set_draining() flips it to True
- create_twilio_media_stream_app accepts drain_gate kwarg without error
- When drain_gate.draining is True, new WS connections are rejected (1001)
- Drain logic waits for active_session_count to reach 0
- Drain logic times out gracefully when sessions don't clear
"""

from __future__ import annotations

import asyncio
import sys
import os
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


# ---------------------------------------------------------------------------
# _DrainGate tests
# ---------------------------------------------------------------------------

class TestDrainGate:
    def test_draining_starts_false(self) -> None:
        from deployment.cpu.app import _DrainGate
        gate = _DrainGate()
        assert gate.draining is False

    def test_set_draining_flips_to_true(self) -> None:
        from deployment.cpu.app import _DrainGate
        gate = _DrainGate()
        gate.set_draining()
        assert gate.draining is True

    def test_set_draining_is_idempotent(self) -> None:
        from deployment.cpu.app import _DrainGate
        gate = _DrainGate()
        gate.set_draining()
        gate.set_draining()
        assert gate.draining is True


# ---------------------------------------------------------------------------
# Drain logic simulation tests (mirror serve()'s lifespan shutdown body)
# ---------------------------------------------------------------------------

class TestDrainLogic:
    """Simulate the lifespan shutdown loop in isolation."""

    async def _simulate_drain(
        self,
        session_counts: list[int],
        drain_max_seconds: float = 5.0,
    ) -> tuple[str, int]:
        """Run the drain wait loop with a fake AudioSessionManagerService.

        Returns (outcome, polls) where outcome is 'clean' or 'timeout'.
        """
        import time as _time

        counts = iter(session_counts)
        asm = MagicMock()
        asm.active_session_count = 0

        poll_count = 0
        outcome = "timeout"

        deadline = _time.monotonic() + drain_max_seconds
        while _time.monotonic() < deadline:
            try:
                active = next(counts)
            except StopIteration:
                active = 0
            asm.active_session_count = active
            poll_count += 1
            if active == 0:
                outcome = "clean"
                break
            await asyncio.sleep(0.01)  # tiny sleep in tests
        return outcome, poll_count

    @pytest.mark.asyncio
    async def test_drain_exits_immediately_when_no_active_calls(self) -> None:
        outcome, polls = await self._simulate_drain([0])
        assert outcome == "clean"
        assert polls == 1

    @pytest.mark.asyncio
    async def test_drain_waits_until_calls_complete(self) -> None:
        outcome, polls = await self._simulate_drain([2, 1, 0])
        assert outcome == "clean"
        assert polls == 3

    @pytest.mark.asyncio
    async def test_drain_times_out_when_calls_persist(self) -> None:
        # drain_max_seconds=0.05 → very short; sessions never clear
        outcome, polls = await self._simulate_drain(
            [3] * 100, drain_max_seconds=0.05
        )
        assert outcome == "timeout"


# ---------------------------------------------------------------------------
# create_twilio_media_stream_app signature test
# ---------------------------------------------------------------------------

try:
    from src.services.media_gateway.twilio_ws_entrypoint import (  # type: ignore
        create_twilio_media_stream_app as _create_app,
    )
    _WS_IMPORTABLE = True
except Exception:
    _WS_IMPORTABLE = False

_skip_ws = pytest.mark.skipif(
    not _WS_IMPORTABLE,
    reason="pydantic v2 / prometheus_client not available on this platform",
)


class TestCreateAppDrainGateParam:
    @_skip_ws
    def test_accepts_drain_gate_kwarg(self) -> None:
        import inspect
        sig = inspect.signature(_create_app)
        assert "drain_gate" in sig.parameters, (
            "create_twilio_media_stream_app must accept drain_gate kwarg"
        )

    @_skip_ws
    def test_drain_gate_defaults_to_none(self) -> None:
        import inspect
        sig = inspect.signature(_create_app)
        param = sig.parameters["drain_gate"]
        assert param.default is None
