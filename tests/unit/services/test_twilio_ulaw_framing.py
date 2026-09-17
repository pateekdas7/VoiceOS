"""Phase I / Gate 1 — TwilioMediaStreams μ-law framing contract test.

The Twilio Media Streams outbound contract requires every audio frame to
be exactly 160 bytes (20 ms of μ-law @ 8 kHz mono). Non-160-byte payloads
are silently dropped by Twilio, so the previous per-clause chunking in
CallOrchestrator._send_clause leaked one malformed frame per clause
whenever len(pcm_ulaw) % 160 != 0. This test locks in the stream-aware
carry-forward framing that fixes that.

Scope: exercises _send_clause via a stubbed adapter/audio-output/scheduler
— no real Kaggle GPU, no real Twilio, no real WebSocket. Gate 2 covers
the live Veena PCM path.
"""
from __future__ import annotations

import asyncio
import types
from dataclasses import dataclass, field
from typing import List

import pytest

from src.libs.contracts.streaming import AudioClause
from src.services.media_gateway.twilio_ws_entrypoint import CallOrchestrator


# ---------------------------------------------------------------------------
# Test doubles — only enough surface for _send_clause / _send_clauses.
# ---------------------------------------------------------------------------


class _FakePlayback:
    """Just the two knobs _send_clause reads: .generation and .dequeue_nowait."""

    def __init__(self, generation: int = 0) -> None:
        self.generation = generation

    def dequeue_nowait(self):  # noqa: D401 — matches PlaybackScheduler shape
        return None


class _FakeAudioOutput:
    """Return the clause bytes verbatim; the framing logic under test is
    downstream of AudioOutput, so this bypasses PCM→μ-law conversion and
    tests the framing loop directly with known μ-law byte streams."""

    def convert(self, clause, fmt: str = "ulaw") -> bytes:  # noqa: D401
        assert fmt == "ulaw"
        return clause.audio_data


class _FakeAdapter:
    """Capture every emitted frame's payload for byte-exact assertions."""

    def __init__(self) -> None:
        self.frames: List[bytes] = []

    async def send_frame(self, frame) -> None:
        # AudioFrame.pcm_data holds the μ-law payload for outbound frames.
        self.frames.append(bytes(frame.pcm_data))


class _FakeVAD:
    def set_playback_active(self, active: bool, playback_seq: int = 0) -> None:  # noqa: D401
        pass


# ---------------------------------------------------------------------------
# Orchestrator builder — sidesteps CallOrchestrator.__init__'s heavy
# construction path (which pulls in VAD/session/dependency graph); the
# framing surface only reads a handful of attributes.
# ---------------------------------------------------------------------------


def _make_orchestrator(generation: int = 0) -> tuple[CallOrchestrator, _FakeAdapter, _FakePlayback]:
    orch = CallOrchestrator.__new__(CallOrchestrator)
    adapter = _FakeAdapter()
    playback = _FakePlayback(generation=generation)

    orch._adapter = adapter
    orch._playback = playback
    orch._audio_output = _FakeAudioOutput()
    orch._vad = _FakeVAD()
    orch._recorder = None
    orch._out_seq = 0
    orch._send_ulaw_carry = b""
    orch._pace_last_clause_end = None
    return orch, adapter, playback


def _clause(payload: bytes, idx: int = 0, generation: int = 0, sample_rate: int = 8000) -> AudioClause:
    return AudioClause(
        audio_data=payload,
        sample_rate=sample_rate,
        text=f"t{idx}",
        clause_index=idx,
        is_final=False,
        generation=generation,
    )


# ---------------------------------------------------------------------------
# Case A — every emitted payload is exactly 160 bytes across 240/320/401/159/80.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_emitted_frame_is_exactly_160_bytes() -> None:
    orch, adapter, _ = _make_orchestrator()
    sizes = [240, 320, 401, 159, 80]
    payloads = [bytes([(i + 1) % 256]) * n for i, n in enumerate(sizes)]

    for idx, payload in enumerate(payloads):
        clause = _clause(payload, idx=idx)
        await orch._send_clause(clause)

    assert adapter.frames, "no frames emitted"
    for f in adapter.frames:
        assert len(f) == 160, f"non-160-byte payload emitted: {len(f)}"

    # Sequence numbers must be monotonic 1..N with no gaps.
    assert orch._out_seq == len(adapter.frames)


# ---------------------------------------------------------------------------
# Case B — byte-reconstruction: emitted frames + discarded final carry
# reconstructs the exact concatenated μ-law byte stream (no duplicates,
# no dropped bytes, no reordering).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_byte_reconstruction_across_all_clauses() -> None:
    orch, adapter, _ = _make_orchestrator()
    sizes = [240, 320, 401, 159, 80]
    payloads = [bytes([(i + 1) % 256]) * n for i, n in enumerate(sizes)]

    for idx, payload in enumerate(payloads):
        await orch._send_clause(_clause(payload, idx=idx))

    emitted = b"".join(adapter.frames)
    final_tail = orch._send_ulaw_carry  # intentionally discarded on call end
    reconstructed = emitted + final_tail

    expected = b"".join(payloads)
    assert reconstructed == expected, "carry-aware framing lost or duplicated bytes"

    # Sanity: final carry < 160 bytes and equals total_bytes % 160.
    assert len(final_tail) < 160
    assert len(final_tail) == sum(sizes) % 160


# ---------------------------------------------------------------------------
# Case C — cross-clause boundary: clause 1 leaves <160-byte tail; clause 2
# completes the next 160-byte frame using that tail as its prefix.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_clause_boundary_carry_completes_next_frame() -> None:
    orch, adapter, _ = _make_orchestrator()

    # 100 bytes: emits 0 frames, carry = 100 bytes of 0xAA.
    await orch._send_clause(_clause(b"\xAA" * 100, idx=0))
    assert len(adapter.frames) == 0
    assert orch._send_ulaw_carry == b"\xAA" * 100

    # 60 more bytes of 0xBB: total buf = 160 bytes → exactly one frame,
    # first 100 bytes 0xAA (from clause 0) + 60 bytes 0xBB (from clause 1).
    await orch._send_clause(_clause(b"\xBB" * 60, idx=1))
    assert len(adapter.frames) == 1
    frame = adapter.frames[0]
    assert len(frame) == 160
    assert frame == (b"\xAA" * 100) + (b"\xBB" * 60), \
        "cross-clause carry did not concatenate correctly"
    assert orch._send_ulaw_carry == b"", "carry should be empty after boundary flush"


# ---------------------------------------------------------------------------
# Case D — generation bump mid-clause: current clause stops sending,
# stale carry is discarded, subsequent clause on new generation starts
# with clean carry.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generation_bump_mid_clause_drops_stale_carry() -> None:
    orch, adapter, playback = _make_orchestrator(generation=0)

    # Leave 90 bytes of pre-barge-in carry.
    await orch._send_clause(_clause(b"\xCC" * 90, idx=0, generation=0))
    assert orch._send_ulaw_carry == b"\xCC" * 90
    assert adapter.frames == []

    # Adapter that trips the generation right after the first frame is sent.
    class _TripAdapter(_FakeAdapter):
        def __init__(self, playback):
            super().__init__()
            self._playback = playback

        async def send_frame(self, frame):
            await super().send_frame(frame)
            if len(self.frames) == 1:
                self._playback.generation = 1  # simulate barge-in

    orch._adapter = _TripAdapter(playback)

    # 400 bytes with prior 90-byte carry gives 490 bytes = 3 full frames
    # + 10 tail. After frame #1 the generation flips, so we expect exactly
    # 1 frame emitted and the carry cleared to b"".
    long_clause = _clause(b"\xDD" * 400, idx=1, generation=0)
    await orch._send_clause(long_clause)

    assert len(orch._adapter.frames) == 1, "mid-clause barge-in should stop after one frame"
    assert orch._send_ulaw_carry == b"", "stale carry must be discarded on gen bump"

    # New generation: clause arrives at the correct generation and framing restarts clean.
    orch2, adapter2, pb2 = _make_orchestrator(generation=1)
    await orch2._send_clause(_clause(b"\xEE" * 320, idx=0, generation=1))
    assert len(adapter2.frames) == 2
    for f in adapter2.frames:
        assert len(f) == 160
        assert f == b"\xEE" * 160
    assert orch2._send_ulaw_carry == b""


# ---------------------------------------------------------------------------
# Case E — sequence numbers and RTP timestamps are monotonic and 20 ms-spaced.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequence_and_rtp_ts_monotonic_20ms_spaced() -> None:
    orch, adapter, _ = _make_orchestrator()

    class _SeqAdapter(_FakeAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.seqs: List[int] = []
            self.rtp_ts: List[int] = []

        async def send_frame(self, frame) -> None:
            await super().send_frame(frame)
            self.seqs.append(frame.seq)
            self.rtp_ts.append(frame.rtp_ts)

    orch._adapter = _SeqAdapter()

    # Five clauses summing to a multiple of 160 so there is zero final tail
    # (960 bytes → exactly 6 frames of 160). Case B already exercises the
    # remainder path; this test focuses on monotonicity.
    for i, n in enumerate([160, 160, 160, 160, 320]):
        await orch._send_clause(_clause(b"\x01" * n, idx=i))

    seqs = orch._adapter.seqs
    rtp = orch._adapter.rtp_ts
    assert seqs == list(range(1, len(seqs) + 1)), "seq must be monotonic starting at 1"
    assert rtp == [s * 160 for s in seqs], "rtp_ts must be seq*160 (20 ms @ 8 kHz)"
    # 20 ms per frame @ 8 kHz μ-law = 160 samples = 160 μ-law bytes.
    for a, b in zip(rtp, rtp[1:]):
        assert b - a == 160, "rtp_ts spacing must be exactly 160 samples per frame"
