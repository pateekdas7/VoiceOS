"""PacketLossConcealer — synthesises audio for lost RTP packets.

When the jitter buffer detects a sequence gap (lost packets), the PLC
generates replacement frames by repeating the last received real frame
with an exponential amplitude fade-out.  This preserves continuity for
short bursts (up to MAX_CONCEAL_FRAMES) without introducing silence
artifacts.

G.711 PLC strategy (V1 Ch4):
  - Frame 0: last real frame x 1.0  (full amplitude)
  - Frame 1: last real frame x 0.5  (-6 dB)
  - Frame 2: last real frame x 0.25 (-12 dB)

G.722 uses the same waveform-similarity repeat approach.

Architecture: V1 Ch4 (PacketLossConcealer — G.711/G.722 PLC).
"""

from __future__ import annotations

import struct

from src.libs.contracts.audio import AudioFrame


class PacketLossConcealer:
    """Synthesises replacement frames for lost RTP packets.

    Feed each received real frame with ``feed()`` so the PLC always has
    a current reference.  When a sequence gap is detected, call
    ``conceal()`` to obtain the replacement frames.  All synthesised
    frames carry ``is_plc=True`` so downstream consumers can discount them.

    Architecture: V1 Ch4 (Audio Session Manager — PacketLossConcealer).
    """

    MAX_CONCEAL_FRAMES: int = 3
    """Maximum consecutive frames synthesised by the PLC.

    Gaps exceeding this limit produce silence beyond frame 3; callers should
    treat extended loss as a session error rather than relying on PLC.
    """

    def __init__(self) -> None:
        self._last_frame: AudioFrame | None = None

    # ------------------------------------------------------------------
    # Feed path
    # ------------------------------------------------------------------

    def feed(self, frame: AudioFrame) -> None:
        """Record a real frame as the PLC reference.

        PLC-synthesised frames are ignored so the reference always reflects
        the last genuine network audio.

        Args:
            frame: A real (not PLC) audio frame received from the transport.
        """
        if not frame.is_plc:
            self._last_frame = frame

    # ------------------------------------------------------------------
    # Concealment
    # ------------------------------------------------------------------

    def conceal(self, n_frames: int, start_seq: int, start_rtp_ts: int) -> list[AudioFrame]:
        """Generate up to n_frames concealment frames.

        Returns an empty list when no reference frame is available (i.e.
        before any real frame has been received).

        Frames are generated with an exponential amplitude fade-out:
          frame 0 → factor 1.0, frame 1 → 0.5, frame 2 → 0.25.
        Capped at MAX_CONCEAL_FRAMES even if n_frames is larger.

        Args:
            n_frames:     Number of concealment frames requested.
            start_seq:    Sequence number of the first synthesised frame.
            start_rtp_ts: RTP timestamp of the first synthesised frame.

        Returns:
            List of AudioFrames with ``is_plc=True``, length ≤ MAX_CONCEAL_FRAMES.
        """
        if self._last_frame is None:
            return []

        n = min(n_frames, self.MAX_CONCEAL_FRAMES)
        ref = self._last_frame
        rtp_increment = ref.config.frame_duration_ms * (ref.config.sample_rate.value // 1000)
        ref_pcm = bytearray(ref.pcm_data)

        result: list[AudioFrame] = []
        for i in range(n):
            fade_factor = 1.0 / (2**i)
            faded_pcm = self._fade(ref_pcm, fade_factor)
            frame = AudioFrame(
                pcm_data=bytes(faded_pcm),
                seq=start_seq + i,
                rtp_ts=start_rtp_ts + i * rtp_increment,
                recv_ts=ref.recv_ts,
                config=ref.config,
                is_plc=True,
            )
            result.append(frame)

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fade(pcm: bytearray, factor: float) -> bytearray:
        """Scale all PCM16LE samples by ``factor``.

        Samples are clamped to [-32768, 32767] after scaling.

        Args:
            pcm:    Source PCM16LE audio bytes (must be even length).
            factor: Linear amplitude multiplier in [0.0, 1.0].

        Returns:
            New bytearray of the same length with scaled samples.
        """
        result = bytearray(len(pcm))
        for offset in range(0, len(pcm) - 1, 2):
            raw: tuple[int, ...] = struct.unpack_from("<h", pcm, offset)
            sample: int = raw[0]
            scaled: int = max(-32768, min(32767, int(sample * factor)))
            struct.pack_into("<h", result, offset, scaled)
        return result
