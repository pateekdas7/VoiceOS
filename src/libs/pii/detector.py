"""PIIDetector — deterministic pattern-based PII detection (V4 Ch10 §10.7, §10.12).

Reuses the same "no ML model required" philosophy as the existing
``src.engines.entity_extraction`` engine (V2 Ch9, Sprint-010): deterministic
regex/validators for structured PII (Aadhaar, PAN, phone, account number,
UPI ID) plus a capitalization heuristic for names, rather than a parallel
NER model (V4 Ch10 §10.3 "reuse existing hooks... rather than a parallel
detector"; §10.20 lists ML-based detection as a *future* improvement, not a
Sprint-020 requirement).

Architecture: V4 Ch10 (PII Protection) §10.7 (Public Interfaces), §10.12
(Algorithms — detection, confidence-scored, layered).
"""

from __future__ import annotations

import re
from re import Pattern

from .entities import PIIEntity, PIISpan

# Ordered highest-priority-first: earlier patterns claim their span before
# later, looser patterns are tried, so e.g. a 12-digit Aadhaar number is
# never re-claimed as a looser ACCOUNT_NUMBER match.
_PATTERNS: tuple[tuple[PIIEntity, Pattern[str], float], ...] = (
    (PIIEntity.AADHAAR, re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}\b"), 0.95),
    (PIIEntity.AADHAAR, re.compile(r"\b\d{12}\b"), 0.7),
    (PIIEntity.PAN, re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), 0.95),
    (PIIEntity.UPI_ID, re.compile(r"\b[\w.\-]{2,256}@[a-zA-Z][a-zA-Z]{2,64}\b"), 0.9),
    (PIIEntity.PHONE, re.compile(r"(?:\+91[-\s]?|0)?\b[6-9]\d{9}\b"), 0.9),
    (PIIEntity.ACCOUNT_NUMBER, re.compile(r"\b\d{10,18}\b"), 0.6),
    (PIIEntity.AMOUNT, re.compile(r"(?:₹|rs\.?\s?|inr\s?)\s?\d[\d,]*(?:\.\d+)?", re.IGNORECASE), 0.8),
    (PIIEntity.NAME, re.compile(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+){1,2}\b"), 0.6),
)


class PIIDetector:
    """Detects PII spans in free text via layered deterministic patterns."""

    def detect(self, text: str) -> list[PIISpan]:
        """Return all detected :class:`PIISpan` occurrences in ``text``.

        Spans never overlap: once a range of ``text`` is claimed by a
        higher-priority pattern, lower-priority patterns cannot re-claim it
        (V4 Ch10 §10.12 "layered detection").
        """
        claimed: list[tuple[int, int]] = []
        spans: list[PIISpan] = []

        for entity_type, pattern, confidence in _PATTERNS:
            for match in pattern.finditer(text):
                start, end = match.span()
                if any(start < c_end and end > c_start for c_start, c_end in claimed):
                    continue
                claimed.append((start, end))
                spans.append(
                    PIISpan(
                        start=start,
                        end=end,
                        entity_type=entity_type,
                        value=match.group(),
                        confidence=confidence,
                    )
                )

        spans.sort(key=lambda span: span.start)
        return spans
