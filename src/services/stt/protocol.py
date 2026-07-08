"""STTAdapter — abstract protocol for Speech-to-Text adapters.

All STT backends implement this protocol.  The rest of the system depends
only on STTAdapter — never on a concrete model class.

Architecture: V1 Ch8 (STT streaming); DocSuite-02 (Interface Contracts).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from src.libs.contracts.audio import AudioFrame
from src.libs.contracts.streaming import WordHypothesis


@runtime_checkable
class STTAdapter(Protocol):
    """Streaming Speech-to-Text adapter interface.

    Implementations wrap a specific ASR backend (e.g. Whisper) and expose
    a uniform streaming contract.  The adapter streams WordHypothesis objects
    as recognition progresses — callers never see model-specific types.

    All implementations MUST request a VRAM allocation from the GPU Scheduler
    before running inference, and release it when done (V7 Ch6).

    Architecture: V1 Ch8.
    """

    async def transcribe_stream(
        self,
        audio_frames: AsyncIterator[AudioFrame],
        language: str,
    ) -> AsyncIterator[WordHypothesis]:
        """Transcribe audio frames, streaming recognised words.

        Args:
            audio_frames: Async stream of PCM16LE AudioFrames at 16 kHz.
            language:     BCP-47 language code (e.g. ``'en'``, ``'hi'``).

        Yields:
            WordHypothesis for each recognised word.  ``is_final=True`` on
            the last hypothesis of the utterance.
        """
        ...
