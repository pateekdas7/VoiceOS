"""Codec-fidelity round-trip test for the μ-law ↔ PCM16 chain (BLOCKER #5).

The framing contract is already covered by test_twilio_ulaw_framing.py
(byte-exact 160-byte frames, monotonic seq/rtp_ts, carry-over across
clauses). What was missing until now is proof that the actual audio
*signal* survives the encode/decode chain intact — i.e. that a synthesized
tone played out through ``AudioOutput.convert(ulaw)`` and then decoded
back through ``_mulaw_frame_to_pcm16le`` produces audio recognisably
similar to the source.

A real Twilio recording sample would be nice for cross-checking network
framing quirks, but the codec math itself is fully deterministic — a
1 kHz sine round-trip is sufficient to catch endian bugs, sample-rate
resampler misuse, or mislabelled μ-law/A-law dispatch, any of which
would silently produce garbled dead-air on the first live call.

Fidelity thresholds are set for μ-law's ~13-bit dynamic range: SNR ≥ 20 dB
is comfortably above the codec floor for a mid-band tone.
"""

from __future__ import annotations

import audioop
import math
import struct

import pytest

from src.libs.contracts.streaming import AudioClause
from src.services.playback.output import AudioOutput

_SRC_RATE_HZ = 24_000  # Veena native
_WIRE_RATE_HZ = 8_000  # Twilio wire
_TONE_HZ = 1_000
_DURATION_S = 0.5
_AMPLITUDE = 12_000  # well below int16 clip (32_767); avoids codec saturation


def _sine_pcm16(rate_hz: int, freq_hz: int, duration_s: float, amp: int) -> bytes:
    """Generate a mono PCM16LE sine wave — stdlib only, no numpy."""
    n = int(rate_hz * duration_s)
    samples = [int(amp * math.sin(2.0 * math.pi * freq_hz * i / rate_hz)) for i in range(n)]
    return struct.pack("<" + "h" * n, *samples)


def _snr_db(reference: bytes, signal: bytes) -> float:
    """Signal-to-noise ratio in dB between two equal-length PCM16LE buffers.

    Length mismatches are trimmed to the shorter of the two — audioop.ratecv
    can produce +/-1 sample drift at buffer boundaries which is normal and
    orthogonal to signal fidelity.
    """
    n = min(len(reference), len(signal)) // 2
    ref = struct.unpack("<" + "h" * n, reference[: n * 2])
    sig = struct.unpack("<" + "h" * n, signal[: n * 2])

    signal_power = sum(r * r for r in ref)
    noise_power = sum((r - s) * (r - s) for r, s in zip(ref, sig))
    if noise_power == 0:
        return float("inf")
    if signal_power == 0:
        return float("-inf")
    return 10.0 * math.log10(signal_power / noise_power)


class TestOutboundCodecFidelity:
    """24 kHz PCM16 (Veena TTS output) → ratecv → μ-law → decoded back."""

    def _make_clause(self, pcm: bytes) -> AudioClause:
        return AudioClause(
            audio_data=pcm,
            sample_rate=_SRC_RATE_HZ,
            text="tone",
            clause_index=0,
            is_final=True,
            generation=0,
        )

    def test_ulaw_encoded_frame_length_matches_downsample_ratio(self) -> None:
        """24 kHz → 8 kHz is a 3:1 downsample; μ-law is 1 byte/sample.
        A 12 000-sample PCM16 buffer (24 kbytes) should encode to ~4 000 μ-law
        bytes (allow +/-1 for ratecv boundary conditions)."""
        pcm = _sine_pcm16(_SRC_RATE_HZ, _TONE_HZ, _DURATION_S, _AMPLITUDE)
        assert len(pcm) == int(_SRC_RATE_HZ * _DURATION_S) * 2

        ulaw = AudioOutput().convert(self._make_clause(pcm), fmt="ulaw")
        expected_bytes = int(_WIRE_RATE_HZ * _DURATION_S)
        assert abs(len(ulaw) - expected_bytes) <= 1, (
            f"μ-law length {len(ulaw)} deviates from expected {expected_bytes} "
            "by more than one sample — resample ratio or codec dispatch is wrong"
        )

    def test_ulaw_roundtrip_snr_above_codec_floor(self) -> None:
        """Reference tone at 8 kHz → μ-law → decode should keep SNR ≥ 20 dB.

        This locks in the entire outbound-side chain: audioop.ratecv +
        audioop.lin2ulaw + audioop.ulaw2lin. A regression that mislabelled
        the codec (e.g. lin2alaw instead of lin2ulaw) would push SNR
        deep into the single digits and trip this test immediately."""
        pcm_24k = _sine_pcm16(_SRC_RATE_HZ, _TONE_HZ, _DURATION_S, _AMPLITUDE)
        pcm_8k_reference = _sine_pcm16(_WIRE_RATE_HZ, _TONE_HZ, _DURATION_S, _AMPLITUDE)

        ulaw = AudioOutput().convert(self._make_clause(pcm_24k), fmt="ulaw")
        pcm_8k_roundtripped = audioop.ulaw2lin(ulaw, 2)

        snr = _snr_db(pcm_8k_reference, pcm_8k_roundtripped)
        assert snr >= 20.0, f"round-trip SNR {snr:.1f} dB below μ-law codec floor (20 dB)"

    def test_ulaw_dispatch_differs_from_alaw(self) -> None:
        """Sanity: ulaw and alaw dispatch must produce byte-different outputs
        for the same input — otherwise the fmt= parameter is a no-op and
        every downstream telephony peer would receive the wrong codec label."""
        pcm = _sine_pcm16(_SRC_RATE_HZ, _TONE_HZ, _DURATION_S, _AMPLITUDE)
        clause = self._make_clause(pcm)
        ulaw = AudioOutput().convert(clause, fmt="ulaw")
        alaw = AudioOutput().convert(clause, fmt="alaw")
        assert ulaw != alaw
        assert len(ulaw) == len(alaw)


class TestInboundCodecFidelity:
    """Twilio wire (μ-law 8 kHz) → PCM16LE 8 kHz.

    ``_mulaw_frame_to_pcm16le`` in twilio_ws_entrypoint is a one-line
    ``audioop.ulaw2lin(frame.pcm_data, 2)`` wrapper (kept there so it can
    also stamp the AudioFrame's ``config`` field with the decoded
    PCM16/8 kHz metadata). Testing the underlying audioop call directly
    keeps this test importable in environments that don't have the full
    ``twilio_ws_entrypoint`` transitive dependency graph (numpy, etc.).
    """

    def test_inbound_decode_doubles_byte_length(self) -> None:
        """μ-law is 1 byte/sample; decoded PCM16LE is 2 bytes/sample.
        Length must exactly double — an off-by-one here would desync
        subsequent VAD/framing math that assumes 8 kHz PCM16."""
        pcm_8k = _sine_pcm16(_WIRE_RATE_HZ, _TONE_HZ, _DURATION_S, _AMPLITUDE)
        ulaw = audioop.lin2ulaw(pcm_8k, 2)

        decoded = audioop.ulaw2lin(ulaw, 2)
        assert len(decoded) == 2 * len(ulaw)

    def test_inbound_ulaw_roundtrip_snr_above_codec_floor(self) -> None:
        pcm_8k = _sine_pcm16(_WIRE_RATE_HZ, _TONE_HZ, _DURATION_S, _AMPLITUDE)
        roundtripped = audioop.ulaw2lin(audioop.lin2ulaw(pcm_8k, 2), 2)
        snr = _snr_db(pcm_8k, roundtripped)
        assert snr >= 25.0, f"inbound-side μ-law round-trip SNR {snr:.1f} dB is unexpectedly low"


class TestFullLoopback:
    """The E2E chain the BLOCKER refers to: TTS PCM → outbound encode →
    (wire) → inbound decode → analyse. A live Twilio recording would prove
    the same thing plus the network framing; that framing is separately
    covered by test_twilio_ulaw_framing.py."""

    def test_full_loopback_snr_above_codec_floor(self) -> None:
        pcm_24k = _sine_pcm16(_SRC_RATE_HZ, _TONE_HZ, _DURATION_S, _AMPLITUDE)
        pcm_8k_reference = _sine_pcm16(_WIRE_RATE_HZ, _TONE_HZ, _DURATION_S, _AMPLITUDE)

        clause = AudioClause(
            audio_data=pcm_24k,
            sample_rate=_SRC_RATE_HZ,
            text="loopback",
            clause_index=0,
            is_final=True,
            generation=0,
        )
        wire_bytes = AudioOutput().convert(clause, fmt="ulaw")
        decoded = audioop.ulaw2lin(wire_bytes, 2)

        snr = _snr_db(pcm_8k_reference, decoded)
        assert snr >= 20.0, (
            f"full loopback SNR {snr:.1f} dB — a live call would sound garbled "
            "or dead. Check the ratecv/lin2ulaw/ulaw2lin chain in AudioOutput."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
