"""TTSService — lifecycle manager for the TTS adapter.

Architecture: V1 Ch15-17; V7 Ch6.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from src.libs.contracts.streaming import AudioClause, VoiceConfig
from src.services.tts.protocol import TTSAdapter
from src.services.tts.script_converter import HindiScriptConverter


@dataclass
class TTSServiceConfig:
    """Configuration for the TTS service."""

    model_name: str = "veena"
    """Scheduler model identifier."""

    vram_mb: int = 2048
    """VRAM reservation per synthesis session in MB."""

    base_url: str = "http://localhost:8200"
    """Base URL of the Veena TTS inference server."""

    default_speaker: str = "kavya"
    """Default Veena speaker voice."""

    sample_rate: int = 24000
    """Sample rate of synthesised audio (Hz)."""


class TTSService:
    """Lifecycle-managed façade around a TTSAdapter.

    Includes the Hindi Devanagari conversion stage (Sprint-009 enhancement):
    text chunks are passed through HindiScriptConverter before reaching the
    TTS adapter, so Roman-script Hindi is synthesised with correct phonology.

    Pipeline:
        LLM tokens → HindiScriptConverter → TTSAdapter → AudioClause stream

    Architecture: V1 Ch15-18.
    """

    def __init__(
        self,
        adapter: TTSAdapter,
        config: TTSServiceConfig,
        script_converter: HindiScriptConverter | None = None,
    ) -> None:
        self._adapter = adapter
        self._config = config
        self._converter = script_converter if script_converter is not None else HindiScriptConverter()

    @classmethod
    def create(
        cls,
        adapter: TTSAdapter,
        config: TTSServiceConfig | None = None,
        script_converter: HindiScriptConverter | None = None,
    ) -> TTSService:
        """Factory: build a TTSService from an adapter."""
        return cls(
            adapter=adapter,
            config=config or TTSServiceConfig(),
            script_converter=script_converter,
        )

    async def synthesize_stream(
        self,
        text_chunks: AsyncIterator[str],
        voice_config: VoiceConfig | None = None,
    ) -> AsyncIterator[AudioClause]:
        """Synthesise text chunks into audio clauses.

        Text chunks pass through the HindiScriptConverter before reaching
        the TTS adapter: Roman-script Hindi words are converted to Devanagari
        so Veena pronounces them with correct Hindi phonology.

        Args:
            text_chunks:  Async stream of text chunks from the LLM.
            voice_config: Prosody parameters; uses neutral defaults if None.

        Yields:
            AudioClause objects from the adapter.
        """
        from src.services.tts.metrics import tts_script_conversion_latency_ms

        vc = voice_config or VoiceConfig()

        start = time.monotonic()
        converted_chunks = self._converter.convert_stream(text_chunks)
        elapsed_ms = (time.monotonic() - start) * 1000
        tts_script_conversion_latency_ms.observe(elapsed_ms)

        return await self._adapter.synthesize_stream(converted_chunks, vc)

    @property
    def adapter(self) -> TTSAdapter:
        """The underlying TTSAdapter."""
        return self._adapter

    @property
    def config(self) -> TTSServiceConfig:
        """The service configuration."""
        return self._config

    @property
    def script_converter(self) -> HindiScriptConverter:
        """The Hindi script converter stage."""
        return self._converter
