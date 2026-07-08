"""ClauseSplitter — splits streaming LLM text into natural speech clauses.

The True Streaming Pipeline (V1 Ch18) synthesises speech clause-by-clause
so playback can begin before generation completes.  ClauseSplitter is the
boundary detector that decides when enough text has accumulated to send
to the TTS engine.

Boundary markers (with trailing space or end-of-stream):
  Strong: `. ` `? ` `! ` `। `  — always split (sentence endings)
  Weak:   `, `                  — split on comma boundaries

The splitter is stateful: call ``feed()`` with each text chunk as it
arrives from the LLM, and ``flush()`` at end of stream to emit any
remaining text.

Architecture: V1 Ch18 (True Streaming Pipeline); DocSuite-07 (Voice Style).
"""

from __future__ import annotations

_STRONG_BOUNDARIES: tuple[str, ...] = (". ", "? ", "! ", "। ")
_WEAK_BOUNDARIES: tuple[str, ...] = (", ",)
_ALL_BOUNDARIES: tuple[str, ...] = _STRONG_BOUNDARIES + _WEAK_BOUNDARIES


class ClauseSplitter:
    """Stateful clause boundary detector for streaming LLM text.

    Thread-unsafe — one instance per active synthesis session.

    Example::

        splitter = ClauseSplitter()
        for token in llm_stream:
            clauses = splitter.feed(token)
            for clause in clauses:
                await tts.synthesize(clause)
        final = splitter.flush()
        if final:
            await tts.synthesize(final)

    Architecture: V1 Ch18.
    """

    def __init__(self) -> None:
        self._buffer: str = ""

    def feed(self, text: str) -> list[str]:
        """Feed a text chunk and return any complete clauses.

        A clause is complete when the buffer contains a boundary marker
        (strong or weak) with text following it.

        Args:
            text: A text chunk from the LLM token stream.

        Returns:
            Zero or more complete clause strings (may be empty list).
        """
        self._buffer += text
        clauses: list[str] = []

        while True:
            boundary_pos = self._next_boundary()
            if boundary_pos is None:
                break
            clause = self._buffer[:boundary_pos]
            self._buffer = self._buffer[boundary_pos:]
            stripped = clause.strip()
            if stripped:
                clauses.append(stripped)

        return clauses

    def flush(self) -> str | None:
        """Return any remaining buffered text as a final clause.

        Should be called after the LLM stream ends.

        Returns:
            The remaining text (stripped), or ``None`` if the buffer is empty.
        """
        remaining = self._buffer.strip()
        self._buffer = ""
        return remaining if remaining else None

    def reset(self) -> None:
        """Clear internal state for reuse."""
        self._buffer = ""

    def _next_boundary(self) -> int | None:
        """Find the character position just after the next boundary marker.

        Returns:
            Index in ``_buffer`` of the first character AFTER the boundary,
            or ``None`` if no boundary exists yet.
        """
        earliest: int | None = None
        for marker in _ALL_BOUNDARIES:
            idx = self._buffer.find(marker)
            if idx == -1:
                continue
            end = idx + len(marker)
            if earliest is None or end < earliest:
                earliest = end
        return earliest
