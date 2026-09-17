"""GreetingCache — pre-synthesised μ-law bytes for the call-open greeting.

Veena TTS on T4 produces audio at ~0.27x realtime, so live-synthesising the
greeting (a fixed 20-word script rendered from kavya_persona._GREETING_TEMPLATE)
adds ~25s of latency to every call. Since the greeting text is deterministic
per (lender_name, customer_name), we synthesise once at CPU startup, convert
to 8 kHz μ-law (the Twilio wire format), and cache the bytes on disk. On
call-open, _speak_greeting reads the cache and streams 160-byte frames
directly to Twilio at 20 ms cadence — bypassing Veena entirely.

Cache key: SHA1 of the rendered greeting text. If the greeting text
changes for any reason (template edit, different customer name, etc.),
the hash mismatches and the cache silently misses — fallback path
synthesises live.

Cache layout: $VOICEOS_GREETING_CACHE_DIR/greeting_<sha1>.ulaw — raw
μ-law bytes at 8 kHz mono, exactly what Twilio's Media Streams accepts
via the outbound media.payload field (base64-encoded on the wire).

When a faster GPU is available and live TTS ≥ 1x realtime, delete this
module and revert the _speak_greeting branch — the live path already
works.

Architecture: V1 Ch15-17 (Speech Rendering).
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

from src.libs.contracts.streaming import AudioClause, VoiceConfig
from src.services.playback.output import AudioOutput
from src.services.tts.service import TTSService

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = "/opt/voiceos/cache"
_FRAME_BYTES = 160  # μ-law 20 ms @ 8 kHz


class GreetingCache:
    """Pre-synthesised greeting audio, stored as raw μ-law on disk.

    Threadsafe read-only after warm_up() completes. warm_up() is
    idempotent — calling it twice with the same text is a no-op on the
    second call (cache hit skips re-synthesis).
    """

    def __init__(self, cache_dir: str | None = None) -> None:
        base = cache_dir or os.environ.get("VOICEOS_GREETING_CACHE_DIR") or _DEFAULT_CACHE_DIR
        self._cache_dir = Path(base)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def _path_for(self, text: str) -> Path:
        return self._cache_dir / f"greeting_{self._hash_text(text)}.ulaw"

    def get_frames(self, text: str) -> list[bytes] | None:
        """Return the list of 160-byte μ-law frames for text, or None on miss.

        Slicing to 160-byte frames happens here (not at warm-up) so the
        stored file is a plain μ-law blob playable by any wave tool for
        debugging (e.g. sox greeting_<hash>.ulaw ...).
        """
        path = self._path_for(text)
        if not path.exists():
            return None
        try:
            data = path.read_bytes()
        except OSError as exc:
            logger.warning("GreetingCache: read failed for %s (%s) — falling back to live TTS", path, exc)
            return None
        if len(data) < _FRAME_BYTES:
            logger.warning("GreetingCache: %s too short (%d bytes) — falling back to live TTS", path, len(data))
            return None
        # Trim to a whole-frame multiple (dropping <20ms of tail is inaudible).
        usable = (len(data) // _FRAME_BYTES) * _FRAME_BYTES
        return [data[i : i + _FRAME_BYTES] for i in range(0, usable, _FRAME_BYTES)]

    async def warm_up(self, text: str, tts_service: TTSService, voice_config: VoiceConfig) -> bool:
        """Synthesise text and write the μ-law bytes to disk.

        Reuses AudioOutput.convert(fmt='ulaw') — the same path used by
        live playback — so cached audio is byte-identical to what live TTS
        would emit (+25% gain, stateful ratecv from 24kHz→8kHz, ulaw
        encoding). Returns True on success, False otherwise. Never raises;
        the caller (startup path) treats False as a soft failure and
        allows the process to keep running with live-TTS fallback.
        """
        path = self._path_for(text)
        if path.exists() and path.stat().st_size >= _FRAME_BYTES:
            logger.info("GreetingCache: warm-up hit — %s already present (%d bytes)", path, path.stat().st_size)
            return True

        logger.info("GreetingCache: warming up %s (text len=%d)", path, len(text))
        try:
            audio_bytes = await self._synthesize_to_ulaw(text, tts_service, voice_config)
        except Exception:
            logger.exception("GreetingCache: warm-up failed — live TTS will handle greeting")
            return False

        if not audio_bytes or len(audio_bytes) < _FRAME_BYTES:
            logger.warning("GreetingCache: synthesised audio too short (%d bytes) — not caching", len(audio_bytes))
            return False

        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_bytes(audio_bytes)
            os.replace(tmp, path)  # atomic
        except OSError:
            logger.exception("GreetingCache: write failed for %s — live TTS will handle greeting", path)
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            return False

        logger.info("GreetingCache: cached %d μ-law bytes (~%d ms audio) at %s",
                    len(audio_bytes), len(audio_bytes) * 1000 // 8000, path)
        return True

    @staticmethod
    async def _synthesize_to_ulaw(text: str, tts_service: TTSService, voice_config: VoiceConfig) -> bytes:
        """Drive TTSService.synthesize_stream over a one-shot text stream,
        feed each yielded AudioClause through AudioOutput.convert(fmt='ulaw'),
        and concatenate. Byte-identical to the live playback path."""

        async def _one_shot() -> AsyncIterator[str]:
            yield text

        audio_out = AudioOutput()  # fresh state, no cross-clause carry-over
        buf = bytearray()
        async for clause in await tts_service.synthesize_stream(_one_shot(), voice_config):
            ulaw = audio_out.convert(clause, fmt="ulaw")
            buf.extend(ulaw)
        return bytes(buf)
