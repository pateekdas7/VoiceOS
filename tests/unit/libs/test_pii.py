"""Unit tests for src/libs/pii/ (V4 Ch10)."""

from __future__ import annotations

import pytest

from src.libs.pii.detector import PIIDetector
from src.libs.pii.entities import PIIEntity
from src.libs.pii.redactor import PIIRedactor
from src.libs.pii.tokenizer import PIITokenAccessDeniedError, PIITokenizer, PIITokenNotFoundError


class _Actor:
    def __init__(self, role: str) -> None:
        self.role = role


class TestPIIDetector:
    def test_pii_detector_phone(self) -> None:
        detector = PIIDetector()

        spans = detector.detect("please call me on 9876543210 tomorrow")

        phone_spans = [span for span in spans if span.entity_type == PIIEntity.PHONE]
        assert len(phone_spans) == 1
        assert phone_spans[0].value == "9876543210"

    def test_pii_detector_aadhaar(self) -> None:
        detector = PIIDetector()

        spans = detector.detect("my aadhaar is 1234 5678 9012")

        aadhaar_spans = [span for span in spans if span.entity_type == PIIEntity.AADHAAR]
        assert len(aadhaar_spans) == 1
        assert aadhaar_spans[0].value == "1234 5678 9012"

    def test_pii_detector_pan(self) -> None:
        detector = PIIDetector()

        spans = detector.detect("PAN number ABCDE1234F on file")

        assert any(span.entity_type == PIIEntity.PAN and span.value == "ABCDE1234F" for span in spans)

    def test_pii_detector_upi_id(self) -> None:
        detector = PIIDetector()

        spans = detector.detect("pay to rahul@okhdfc now")

        assert any(span.entity_type == PIIEntity.UPI_ID for span in spans)

    def test_pii_detector_does_not_double_count_overlapping_spans(self) -> None:
        detector = PIIDetector()

        spans = detector.detect("aadhaar 1234 5678 9012 confirmed")
        starts = [span.start for span in spans]

        assert len(starts) == len(set(starts))


class TestPIIRedactor:
    def test_pii_redactor_masks_phone(self) -> None:
        redactor = PIIRedactor()

        result = redactor.redact("call 9876543210 please")

        assert "9876543210" not in result
        assert "[PHONE]" in result

    def test_redact_handles_multiple_spans(self) -> None:
        redactor = PIIRedactor()

        result = redactor.redact("aadhaar 1234 5678 9012 phone 9876543210")

        assert "1234 5678 9012" not in result
        assert "9876543210" not in result
        assert "[AADHAAR]" in result
        assert "[PHONE]" in result

    def test_mask_reveals_only_last_four(self) -> None:
        redactor = PIIRedactor()

        masked = redactor.mask("9876543210", PIIEntity.PHONE)

        assert masked == "•" * 6 + "3210"


class TestPIITokenizer:
    def test_tokenize_returns_opaque_token(self) -> None:
        tokenizer = PIITokenizer()

        token = tokenizer.tokenize("9876543210", PIIEntity.PHONE)

        assert token.startswith("PHONE_")
        assert "9876543210" not in token

    def test_detokenize_requires_auditor_role(self) -> None:
        tokenizer = PIITokenizer()
        token = tokenizer.tokenize("9876543210", PIIEntity.PHONE)

        with pytest.raises(PIITokenAccessDeniedError):
            tokenizer.detokenize(token, _Actor("AGENT"))

    def test_detokenize_reverses_for_auditor(self) -> None:
        tokenizer = PIITokenizer()
        token = tokenizer.tokenize("9876543210", PIIEntity.PHONE)

        value = tokenizer.detokenize(token, _Actor("AUDITOR"))

        assert value == "9876543210"

    def test_detokenize_unknown_token_raises(self) -> None:
        tokenizer = PIITokenizer()

        with pytest.raises(PIITokenNotFoundError):
            tokenizer.detokenize("PHONE_doesnotexist", _Actor("AUDITOR"))
