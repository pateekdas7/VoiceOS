"""PII entity vocabulary and detection result types (V4 Ch10 §10.6).

Architecture: V4 Ch10 (PII Protection) §10.6 (Outputs), §10.12 (Detection).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PIIEntity(StrEnum):
    """PII entity types detected/protected across VoiceOS (V4 Ch10 §10.12)."""

    AADHAAR = "AADHAAR"
    """12-digit Indian government ID (Sensitivity: HIGH, V4 Ch10 §10.12)."""

    PAN = "PAN"
    """Indian tax ID, format AAAAA9999A (Sensitivity: HIGH)."""

    PHONE = "PHONE"
    """10-digit Indian mobile number, optionally +91-prefixed (Sensitivity: MEDIUM)."""

    ACCOUNT_NUMBER = "ACCOUNT_NUMBER"
    """Bank/loan account number, 10-18 digits (Sensitivity: HIGH)."""

    UPI_ID = "UPI_ID"
    """UPI payment identifier, e.g. 'name@bank' (Sensitivity: HIGH)."""

    NAME = "NAME"
    """Customer or third-party name (Sensitivity: MEDIUM)."""

    AMOUNT = "AMOUNT"
    """Monetary amount mentioned in free text (Sensitivity: LOW)."""


class Sensitivity(StrEnum):
    """PII sensitivity tier, driving protection strength (V4 Ch10 §10.12)."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


_ENTITY_SENSITIVITY: dict[PIIEntity, Sensitivity] = {
    PIIEntity.AADHAAR: Sensitivity.HIGH,
    PIIEntity.PAN: Sensitivity.HIGH,
    PIIEntity.ACCOUNT_NUMBER: Sensitivity.HIGH,
    PIIEntity.UPI_ID: Sensitivity.HIGH,
    PIIEntity.PHONE: Sensitivity.MEDIUM,
    PIIEntity.NAME: Sensitivity.MEDIUM,
    PIIEntity.AMOUNT: Sensitivity.LOW,
}


def sensitivity_of(entity_type: PIIEntity) -> Sensitivity:
    """Return the configured sensitivity tier for ``entity_type`` (V4 Ch10 §10.12)."""
    return _ENTITY_SENSITIVITY[entity_type]


@dataclass(frozen=True)
class PIISpan:
    """One detected PII occurrence within a text (V4 Ch10 §10.6)."""

    start: int
    end: int
    entity_type: PIIEntity
    value: str
    confidence: float = 1.0
