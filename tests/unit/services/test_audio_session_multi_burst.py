"""Regression tests for AudioSession multi-burst frame delivery.

Prevents recurrence of the E2E-v6 bug where a Twilio-style client that
reset the `chunk` counter on each send burst caused all frames after
the first burst to accumulate in AdaptiveJitterBuffer, never reaching
VAD/STT — pipeline appeared frozen ("no audio after 80s") because
_next_seq had advanced past every subsequent frame's seq.

Real Twilio Media Streams always emit monotonically-increasing `chunk`
values across the whole WSS session; the tests below assert
AudioSession delivers 100% of frames when this contract is honoured.

Placed alongside test_audio_session_manager.py.
"""

from __future__ import annotations

from src.libs.contracts.audio import AudioConfig, AudioFrame, Encoding, SampleRate
from src.services.audio_session_manager.session import AudioSession


_CFG = AudioConfig(
    sample_rate=SampleRate.RATE_8K,
    encoding=Encoding.PCM16LE,
    channels=1,
    frame_duration_ms=20,
)
_SILENCE = b"\x00" * 320  # 20 ms PCM16LE @ 8 kHz


def _f(seq: int) -> AudioFrame:
    return AudioFrame(
        pcm_data=_SILENCE,
        seq=seq,
        rtp_ts=seq * 160,
        recv_ts=seq * 0.02,
        config=_CFG,
    )


class TestMultiBurstMonotonicChunks:
    """A single call sends audio in multiple bursts (silence, speech,
    silence, …). Twilio's `chunk` field is monotonically increasing
    across the whole call, so a run of ``send_audio()`` calls translates
    into a single monotone seq stream. Every pushed frame must emerge
    from ``push_frame()`` — no frames may get stuck in the jitter buffer.
    """

    def test_three_bursts_all_frames_delivered(self) -> None:
        session = AudioSession(call_id="c1", tenant_id="t1")
        emitted: list[int] = []
        # Burst A: 1 s of silence  = 50 frames @ 20 ms
        # Burst B: 2 s of clip     = 100 frames
        # Burst C: 1.5 s of silence = 75 frames
        for seq in range(0, 50 + 100 + 75):
            for out in session.push_frame(_f(seq)):
                emitted.append(out.seq)
        assert emitted == list(range(0, 225)), (
            f"expected 225 monotone frames, got {len(emitted)}: "
            f"first={emitted[:5]} last={emitted[-5:]}"
        )

    def test_larger_multiburst_no_stuck_frames(self) -> None:
        session = AudioSession(call_id="c2", tenant_id="t2")
        emitted: list[int] = []
        # 5 turns × (1 s silence + 2 s clip + 1.5 s silence) = 5 × 225 = 1125
        for seq in range(0, 1125):
            for out in session.push_frame(_f(seq)):
                emitted.append(out.seq)
        assert len(emitted) == 1125
        # No gaps: strictly monotonic 0..1124.
        assert emitted == list(range(0, 1125))
        # Jitter buffer must be drained back to zero at the end
        # (proves nothing is stuck).
        assert session.jitter_buffer.depth == 0

    def test_first_frame_seq_nonzero_still_flushes(self) -> None:
        """Twilio can begin a stream at any chunk index (media msgs start
        after ``connected`` + ``start`` acks). Session must honour whatever
        the first observed seq is as the anchor, not require seq=0."""
        session = AudioSession(call_id="c3", tenant_id="t3")
        emitted: list[int] = []
        for seq in range(1000, 1050):
            for out in session.push_frame(_f(seq)):
                emitted.append(out.seq)
        assert emitted == list(range(1000, 1050))
        assert session.jitter_buffer.depth == 0
