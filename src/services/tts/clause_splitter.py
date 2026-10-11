"""ClauseSplitter — splits streaming LLM text into natural speech clauses.

The True Streaming Pipeline (V1 Ch18) synthesises speech clause-by-clause
so playback can begin before generation completes. ClauseSplitter is the
single authoritative boundary detector: it is used by TrueStreamingPipeline
on the LLM token stream. TTS adapters MUST NOT re-split — they receive one
already-complete clause per synthesise call.

Boundary markers (with trailing whitespace or end-of-stream):
  Strong: `. ` `? ` `! ` `; ` `। `  — always split (sentence/major-clause endings)

Anti-split guards (do not split at ". " when it is not a sentence end):
  - short abbreviation    — "Rs.", "Dr.", "Mr.", "Mrs.", "Ms.", "Ltd.",
                            "Pvt.", "Co.", "St.", "Jr.", "Sr.", "vs.",
                            "etc.", "e.g.", "i.e.", "No.", "Prof.", "Inc.",
                            "Corp.", "a.m.", "p.m.", "U.S." (case-insensitive)

Note: no explicit decimal guard is needed. A real decimal ("3.14") has no
whitespace between digit and ".", so it never matches the ". " marker in
the first place. Currency like "Rs. 5,000." is handled by the abbreviation
guard on "Rs." plus the natural sentence terminator on the trailing ". ".

Design notes:
  - Weak boundaries (commas) intentionally NOT included: splitting sentences
    on commas produces artificial pauses inside a single semantic unit and
    degrades TTS prosody.
  - The abbreviation guard is domain-tuned for the Hindi/Hinglish loan
    collection use case (currency, formal titles).
  - `.` without trailing whitespace does not split — the splitter waits until
    the next character arrives (or `flush()` is called at end of stream), so
    it can distinguish "3.1" (decimal) from "3. " (sentence end).

The splitter is stateful: call ``feed()`` with each text chunk as it
arrives from the LLM, and ``flush()`` at end of stream to emit any
remaining text.

Architecture: V1 Ch18 (True Streaming Pipeline); DocSuite-07 (Voice Style).
"""

from __future__ import annotations

# Strong sentence boundaries — always split when followed by whitespace.
_STRONG_BOUNDARIES: tuple[str, ...] = (". ", "? ", "! ", "; ", "। ")

# Short lowercase-normalised tokens that end in "." but are not sentence ends.
# Guard: when a candidate ". " boundary is found at absolute index `p`, the
# word ending at position `p+1` (i.e., "<word>.") is checked against this set.
_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "rs",
        "dr",
        "mr",
        "mrs",
        "ms",
        "ltd",
        "pvt",
        "co",
        "st",
        "jr",
        "sr",
        "vs",
        "etc",
        "e.g",
        "i.e",
        "no",
        "prof",
        "inc",
        "corp",
        "a.m",
        "p.m",
        "u.s",
        "ph.d",
        "gen",
        "col",
    }
)


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
        with text (or whitespace) following it, and the marker is not
        guarded (decimal number, abbreviation).

        Args:
            text: A text chunk from the LLM token stream.

        Returns:
            Zero or more complete clause strings (may be empty list).
        """
        self._buffer += text
        clauses: list[str] = []

        while True:
            boundary_end = self._next_boundary()
            if boundary_end is None:
                break
            clause = self._buffer[:boundary_end]
            self._buffer = self._buffer[boundary_end:]
            stripped = clause.strip()
            if stripped:
                clauses.append(stripped)

        return clauses

    def flush(self) -> str | None:
        """Return any remaining buffered text as a final clause.

        Should be called after the LLM stream ends. Emits the last utterance
        even when it has no trailing whitespace (e.g., a single-word Hindi
        reply "हाँ।" — the boundary marker is present but was not followed
        by whitespace, so `feed()` correctly held it back).

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
        """Find the character position just after the next unguarded boundary.

        Walks the buffer left-to-right so a single guarded ". " (e.g., "Rs. ")
        does not mask a later unguarded ". " (e.g., end of the same sentence).

        Returns:
            Index in ``_buffer`` of the first character AFTER the boundary,
            or ``None`` if no unguarded boundary exists yet.
        """
        buf = self._buffer
        n = len(buf)
        pos = 0
        while pos < n:
            earliest_marker_pos: int | None = None
            earliest_marker_len: int = 0
            for marker in _STRONG_BOUNDARIES:
                idx = buf.find(marker, pos)
                if idx == -1:
                    continue
                if earliest_marker_pos is None or idx < earliest_marker_pos:
                    earliest_marker_pos = idx
                    earliest_marker_len = len(marker)
            if earliest_marker_pos is None:
                return None
            end = earliest_marker_pos + earliest_marker_len
            # Only ". " needs a guard against decimals + abbreviations.
            if buf[earliest_marker_pos] == "." and self._is_guarded_period(earliest_marker_pos):
                pos = end  # skip this occurrence, keep looking
                continue
            return end
        return None

    def _is_guarded_period(self, dot_idx: int) -> bool:
        """Return True if the ``.`` at ``dot_idx`` is a known abbreviation.

        No decimal guard is needed: a real decimal like "3.14" has no whitespace
        between the digit and the ".", so it never matches the ". " boundary
        marker. Only ". " followed by whitespace reaches this check, which is
        always a sentence terminator unless the preceding token is an
        abbreviation like "Rs." or "Dr.".
        """
        buf = self._buffer
        # Abbreviation guard — the whitespace-separated "word" ending in "."
        # (case-insensitive, may contain embedded dots for "e.g", "a.m", etc).
        wstart = dot_idx
        while wstart > 0 and not buf[wstart - 1].isspace():
            wstart -= 1
        word = buf[wstart:dot_idx].lower()
        return word in _ABBREVIATIONS
