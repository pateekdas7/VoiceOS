"""TTSAdapter — abstract protocol for Text-to-Speech adapters.

All TTS backends implement this protocol.  The rest of the system depends
only on TTSAdapter — never on a concrete model class.

Architecture: V1 Ch15-17 (Speech Rendering / Veena TTS);
              DocSuite-02 (Interface Contracts).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from src.libs.contracts.streaming import AudioClause, VoiceConfig


@runtime_checkable
class TTSAdapter(Protocol):
    """Streaming Text-to-Speech adapter interface.

    Implementations wrap a specific TTS backend (e.g. Veena) and expose
    a uniform clause-level streaming contract.

    The adapter MUST:
    - Accept an async stream of text chunks that together form ONE clause.
      Sentence segmentation is performed upstream by ``ClauseSplitter``
      inside ``TrueStreamingPipeline``; the adapter MUST NOT re-split.
    - Synthesize the clause and yield an AudioClause per audio chunk as it
      is decoded by the backend.
    - Request VRAM from the GPU Scheduler before inference (V7 Ch6).

    Architecture: V1 Ch15-17.
    """

    async def synthesize_stream(
        self,
        text_chunks: AsyncIterator[str],
        voice_config: VoiceConfig,
    ) -> AsyncIterator[AudioClause]:
        """Synthesize an LLM text stream into audio clauses.

        Args:
            text_chunks:  Async stream of text chunks from the LLM.
            voice_config: Prosody parameters from the AdaptiveProsodyEngine.

        Yields:
            AudioClause for each synthesized clause.  ``is_final=True`` on
            the last clause of the response.
        """
        ...
