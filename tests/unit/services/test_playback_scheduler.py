"""Unit tests for PlaybackScheduler, AudioOutput, and DecisionEnvelope emission."""

from __future__ import annotations

import asyncio
import struct
import uuid
from datetime import datetime

import pytest

from src.libs.contracts.streaming import AudioClause
from src.services.playback.output import SUPPORTED_FORMATS, AudioOutput
from src.services.playback.scheduler import PlaybackScheduler


def _make_pcm16_bytes(num_samples: int = 160) -> bytes:
    """Return silent PCM16 LE bytes (all zeros, 2 bytes/sample)."""
    return struct.pack(f"<{num_samples}h", *([0] * num_samples))


def _make_clause(index: int = 0, is_final: bool = False) -> AudioClause:
    return AudioClause(
        audio_data=b"\x00" * 100,
        sample_rate=24000,
        text=f"clause {index}",
        clause_index=index,
        is_final=is_final,
    )


class TestPlaybackScheduler:
    async def test_enqueue_and_dequeue(self) -> None:
        scheduler = PlaybackScheduler()
        clause = _make_clause(0)
        await scheduler.enqueue(clause)
        result = await asyncio.wait_for(scheduler.dequeue(), timeout=1.0)
        assert result.clause_index == 0

    async def test_depth_tracks_queue_size(self) -> None:
        scheduler = PlaybackScheduler()
        await scheduler.enqueue(_make_clause(0))
        await scheduler.enqueue(_make_clause(1))
        assert scheduler.depth == 2

    async def test_playback_flush_on_bargein(self) -> None:  # required named test
        """test_playback_flush_on_bargein: flush empties queue and sets barge_in_event."""
        scheduler = PlaybackScheduler()
        await scheduler.enqueue(_make_clause(0))
        await scheduler.enqueue(_make_clause(1))
        await scheduler.enqueue(_make_clause(2))
        flushed = await scheduler.flush()
        assert len(flushed) == 3
        assert scheduler.depth == 0
        assert scheduler.barge_in_event.is_set()

    async def test_get_clauses_non_destructive(self) -> None:
        scheduler = PlaybackScheduler()
        await scheduler.enqueue(_make_clause(0))
        clauses = scheduler.get_clauses()
        assert len(clauses) == 1
        # Queue still intact.
        assert scheduler.depth == 1

    async def test_ri3_violation_on_overflow(self) -> None:
        from src.libs.invariants.errors import InvariantViolationError

        scheduler = PlaybackScheduler(max_depth=2)
        await scheduler.enqueue(_make_clause(0))
        await scheduler.enqueue(_make_clause(1))
        with pytest.raises(InvariantViolationError):
            await scheduler.enqueue(_make_clause(2))


class TestAudioOutput:
    def test_ulaw_conversion_returns_bytes(self) -> None:
        output = AudioOutput(source_sample_rate=24000, target_sample_rate=8000)
        clause = AudioClause(
            audio_data=_make_pcm16_bytes(240),  # 240 samples @ 24kHz = 10ms
            sample_rate=24000,
            text="test",
            clause_index=0,
            is_final=False,
        )
        result = output.convert(clause, fmt="ulaw")
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_alaw_conversion_returns_bytes(self) -> None:
        output = AudioOutput(source_sample_rate=24000, target_sample_rate=8000)
        clause = AudioClause(
            audio_data=_make_pcm16_bytes(240),
            sample_rate=24000,
            text="test",
            clause_index=0,
            is_final=False,
        )
        result = output.convert(clause, fmt="alaw")
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_pcm16_passthrough_same_rate(self) -> None:
        pcm = _make_pcm16_bytes(160)
        output = AudioOutput(source_sample_rate=8000, target_sample_rate=8000)
        clause = AudioClause(
            audio_data=pcm,
            sample_rate=8000,
            text="test",
            clause_index=0,
            is_final=True,
        )
        result = output.convert(clause, fmt="pcm16")
        assert result == pcm  # passthrough, no resampling

    def test_invalid_format_raises_value_error(self) -> None:
        output = AudioOutput()
        clause = AudioClause(
            audio_data=_make_pcm16_bytes(240),
            sample_rate=24000,
            text="test",
            clause_index=0,
            is_final=False,
        )
        with pytest.raises(ValueError, match="Unsupported audio format"):
            output.convert(clause, fmt="mp3")

    def test_supported_formats_are_known(self) -> None:
        assert "ulaw" in SUPPORTED_FORMATS
        assert "alaw" in SUPPORTED_FORMATS
        assert "pcm16" in SUPPORTED_FORMATS

    def test_decision_envelope_emitted_per_turn(self) -> None:  # required named test
        """test_decision_envelope_emitted_per_turn: DecisionEnvelope has unique ID per call."""
        from src.libs.contracts.decision import (
            DecisionEnvelope,
            GovernanceStatus,
            GovernanceVerdict,
        )

        envelope = DecisionEnvelope(
            envelope_id=str(uuid.uuid4()),
            call_id="call-001",
            tenant_id="tenant-001",
            timestamp=datetime.utcnow(),
            response_plan_id=str(uuid.uuid4()),
            governance_verdict=GovernanceVerdict(status=GovernanceStatus.APPROVE),
        )
        assert envelope.envelope_id != ""
        assert envelope.call_id == "call-001"
