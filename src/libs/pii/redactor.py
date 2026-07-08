"""PIIRedactor — masks detected PII in text before it reaches logs/transcripts.

Applied at emission (V4 Ch10 §10.12 "Redaction: remove/replace PII in logs...
at emission") — wired into ``StructuredLogger`` (V3 Ch16) so no raw PII ever
reaches application logs, and into MongoDB transcript storage.

Architecture: V4 Ch10 (PII Protection) §10.7, §10.12.
"""

from __future__ import annotations

from .detector import PIIDetector
from .entities import PIIEntity

_DEFAULT_DETECTOR = PIIDetector()


class PIIRedactor:
    """Replaces detected PII spans with ``[ENTITY_TYPE]`` placeholders."""

    def __init__(self, detector: PIIDetector | None = None) -> None:
        self._detector = detector or _DEFAULT_DETECTOR

    def redact(self, text: str) -> str:
        """Return ``text`` with every detected PII span replaced by ``[ENTITY_TYPE]``.

        Spans are replaced back-to-front so earlier offsets stay valid as
        later replacements change the string length.
        """
        spans = self._detector.detect(text)
        redacted = text
        for span in sorted(spans, key=lambda s: s.start, reverse=True):
            redacted = f"{redacted[: span.start]}[{span.entity_type.value}]{redacted[span.end :]}"
        return redacted

    def mask(self, value: str, entity_type: PIIEntity) -> str:
        """Deterministic partial reveal for display (V4 Ch10 §10.12), e.g. ``••••1234``.

        Never reversible from the mask — distinct from :class:`PIITokenizer`,
        which is reversible under authorization.
        """
        if len(value) <= 4:
            return "•" * len(value)
        return f"{'•' * (len(value) - 4)}{value[-4:]}"
