"""STTService — lifecycle manager for the STT adapter.

Wraps the STTAdapter and provides factory helpers for building production
and test configurations.

Architecture: V1 Ch8; V7 Ch6.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from src.libs.contracts.audio import AudioFrame
from src.libs.contracts.streaming import WordHypothesis
from src.services.stt.protocol import STTAdapter


@dataclass
class STTServiceConfig:
    """Configuration for the STT service."""

    model_name: str = "whisper-large-v3-turbo"
    """Model identifier used when reserving VRAM in the GPU Scheduler."""

    vram_mb: int = 6144
    """VRAM reservation per inference in MB."""

    default_language: str = "hi"
    """Default transcription language (BCP-47) when caller omits it."""

    beam_size: int = 5
    """Beam search size passed to Whisper."""

    tags: dict[str, str] = field(default_factory=dict)
    """Optional metadata tags for observability."""


class STTService:
    """Lifecycle-managed façade around an STTAdapter.

    Build with STTService.create() for production or inject a custom adapter
    for testing.

    Architecture: V1 Ch8.
    """

    def __init__(self, adapter: STTAdapter, config: STTServiceConfig) -> None:
        self._adapter = adapter
        self._config = config

    @classmethod
    def create(cls, adapter: STTAdapter, config: STTServiceConfig | None = None) -> STTService:
        """Factory: build an STTService from an adapter.

        Args:
            adapter: A conforming STTAdapter implementation.
            config:  Service configuration (uses defaults if omitted).

        Returns:
            A ready-to-use STTService.
        """
        return cls(adapter=adapter, config=config or STTServiceConfig())

    async def transcribe_stream(
        self,
        audio_frames: AsyncIterator[AudioFrame],
        language: str = "",
    ) -> AsyncIterator[WordHypothesis]:
        """Transcribe an audio frame stream.

        Delegates to the underlying adapter.  Falls back to the configured
        default language when ``language`` is empty.

        Args:
            audio_frames: Async stream of PCM16LE frames at 16 kHz.
            language:     BCP-47 language code; uses config default if empty.

        Yields:
            WordHypothesis objects streamed from the adapter.
        """
        lang = language or self._config.default_language
        return await self._adapter.transcribe_stream(audio_frames, lang)

    @property
    def adapter(self) -> STTAdapter:
        """The underlying STTAdapter."""
        return self._adapter

    @property
    def config(self) -> STTServiceConfig:
        """The service configuration."""
        return self._config
