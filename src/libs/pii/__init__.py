"""PII Protection — detection, redaction, tokenization (V4 Ch10).

Architecture: V4 Ch10 (PII Protection).
"""

from __future__ import annotations

from src.libs.pii.detector import PIIDetector
from src.libs.pii.entities import PIIEntity, PIISpan, Sensitivity, sensitivity_of
from src.libs.pii.redactor import PIIRedactor
from src.libs.pii.tokenizer import PIITokenAccessDeniedError, PIITokenizer, PIITokenNotFoundError

__all__ = [
    "PIIDetector",
    "PIIEntity",
    "PIIRedactor",
    "PIISpan",
    "PIITokenAccessDeniedError",
    "PIITokenNotFoundError",
    "PIITokenizer",
    "Sensitivity",
    "sensitivity_of",
]
