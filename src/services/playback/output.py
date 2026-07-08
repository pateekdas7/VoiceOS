"""AudioOutput — audio format conversion for telephony transport.

Converts 24 kHz PCM16 audio from Veena TTS into telephony-compatible formats:
  - μ-law 8 kHz: Twilio, SIP over RTP/PSTN
  - G.711 alaw 8 kHz: traditional PSTN / Asterisk / FreeSWITCH
  - PCM16 (passthrough): WebRTC, internal

Architecture: V1 Ch17 (Veena TTS output); V1 Ch21 (Playback / delivery).
"""

from __future__ import annotations

import audioop
import logging

from src.libs.contracts.streaming import AudioClause

logger = logging.getLogger(__name__)

_VEENA_SAMPLE_RATE = 24_000
_TELEPHONY_SAMPLE_RATE = 8_000

SUPPORTED_FORMATS = frozenset({"ulaw", "alaw", "pcm16"})


class AudioOutput:
    """Converts AudioClause PCM bytes to the requested transport format.

    All conversions are stateless and synchronous. The converted bytes
    are returned directly for the caller to write to the media socket.

    Args:
        source_sample_rate: Input sample rate (default 24000 for Veena).
        target_sample_rate: Output sample rate (default 8000 for telephony).
    """

    def __init__(
        self,
        source_sample_rate: int = _VEENA_SAMPLE_RATE,
        target_sample_rate: int = _TELEPHONY_SAMPLE_RATE,
    ) -> None:
        self._src_rate = source_sample_rate
        self._tgt_rate = target_sample_rate

    def convert(self, clause: AudioClause, fmt: str = "ulaw") -> bytes:
        """Convert an AudioClause to the specified transport format.

        Args:
            clause: The AudioClause from the TTS pipeline.
            fmt: Target format — 'ulaw', 'alaw', or 'pcm16'.

        Returns:
            Converted audio bytes.

        Raises:
            ValueError: If fmt is not a supported format.
        """
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported audio format '{fmt}'. Supported: {SUPPORTED_FORMATS}")

        pcm = clause.audio_data

        # Resample if source and target rates differ.
        if self._src_rate != self._tgt_rate:
            pcm, _ = audioop.ratecv(
                pcm,
                2,  # 2 bytes per sample (PCM16)
                1,  # mono
                self._src_rate,
                self._tgt_rate,
                None,
            )

        if fmt == "ulaw":
            converted = audioop.lin2ulaw(pcm, 2)
        elif fmt == "alaw":
            converted = audioop.lin2alaw(pcm, 2)
        else:  # pcm16 passthrough
            converted = pcm

        logger.debug(
            "AudioOutput: converted clause %d — %d bytes → %d bytes (%s)",
            clause.clause_index,
            len(clause.audio_data),
            len(converted),
            fmt,
        )
        return converted
