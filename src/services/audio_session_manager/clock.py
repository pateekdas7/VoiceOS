"""SessionClock — maps RTP timestamps to wall-clock milliseconds.

Maintains the relationship between the carrier's RTP clock domain and
real wall-clock time. Handles the mandatory 32-bit RTP wrap-around case
for calls longer than ~149 hours at 8 kHz.

Architecture: V1 Ch4 (SessionClock — RTP timestamp → wall clock mapping).
"""

from __future__ import annotations

_MAX_RTP_TIMESTAMP: int = 0xFFFF_FFFF
_HALF_RTP_RANGE: int = 0x7FFF_FFFF


class SessionClock:
    """Maps RTP timestamps to wall-clock milliseconds.

    The first call to ``anchor()`` sets the origin.  Subsequent calls to
    ``timestamp_ms()`` return elapsed milliseconds from that origin using
    the configured sample rate.

    Wrap-around is handled by interpreting the 32-bit delta modulo 2^32
    and treating any delta > 2^31 as a backward jump (clamped to 0).

    Architecture: V1 Ch4 (Audio Session Manager — SessionClock).
    """

    def __init__(self, sample_rate: int = 8000) -> None:
        """Initialise the clock with the audio sample rate.

        Args:
            sample_rate: Samples per second of the RTP stream (default 8000 Hz
                for G.711 telephony).
        """
        self._sample_rate = sample_rate
        self._rtp_origin: int | None = None
        self._wall_origin_ms: float | None = None

    # ------------------------------------------------------------------
    # Anchor
    # ------------------------------------------------------------------

    def anchor(self, rtp_ts: int, wall_ts_s: float) -> None:
        """Set the clock origin from the first RTP packet.

        Idempotent: subsequent calls are no-ops once the origin is set.

        Args:
            rtp_ts:     RTP timestamp of the reference packet.
            wall_ts_s:  Local wall-clock time in seconds since epoch.
        """
        if self._rtp_origin is None:
            self._rtp_origin = rtp_ts
            self._wall_origin_ms = wall_ts_s * 1000.0

    @property
    def is_anchored(self) -> bool:
        """True after the first call to ``anchor()``."""
        return self._rtp_origin is not None

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def timestamp_ms(self, rtp_ts: int) -> float:
        """Convert an RTP timestamp to milliseconds elapsed since call start.

        Returns 0.0 if the clock has not been anchored yet.

        Handles 32-bit RTP timestamp wrap-around by computing the delta
        modulo 2^32 and treating deltas > 2^31 as zero (backward jumps
        are treated as no-ops rather than returning negative values).

        Args:
            rtp_ts: RTP timestamp to convert.

        Returns:
            Milliseconds since the anchor point (≥ 0.0).
        """
        if self._rtp_origin is None:
            return 0.0

        delta = (rtp_ts - self._rtp_origin) & _MAX_RTP_TIMESTAMP
        if delta > _HALF_RTP_RANGE:
            return 0.0
        return (delta / self._sample_rate) * 1000.0

    def wall_ms(self, rtp_ts: int) -> float:
        """Convert an RTP timestamp to absolute wall-clock milliseconds.

        Returns 0.0 if the clock has not been anchored yet.

        Args:
            rtp_ts: RTP timestamp to convert.

        Returns:
            Wall-clock milliseconds since Unix epoch.
        """
        if self._rtp_origin is None or self._wall_origin_ms is None:
            return 0.0
        return self._wall_origin_ms + self.timestamp_ms(rtp_ts)
