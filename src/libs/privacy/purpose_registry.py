"""PurposeRegistry — maps data fields/classes to allowed processing purposes (V4 Ch9 §9.12).

Architecture: V4 Ch9 §9.12 ("Purpose limitation: data tagged with purpose
at capture; processing checks purpose ∈ consented_purposes").
"""

from __future__ import annotations

from enum import StrEnum


class Purpose(StrEnum):
    """Why data is being processed — the DPDP-required processing purpose."""

    COLLECTIONS = "collections"
    MARKETING = "marketing"
    ANALYTICS = "analytics"
    SUPPORT = "support"
    FRAUD_PREVENTION = "fraud_prevention"


class DataClass(StrEnum):
    """Category of PII/data a purpose check or minimization filter operates on."""

    NAME = "name"
    PHONE = "phone"
    ADDRESS = "address"
    FINANCIAL = "financial"
    VOICE_RECORDING = "voice_recording"
    TRANSCRIPT = "transcript"


_DEFAULT_ALLOWED_PURPOSES: dict[DataClass, frozenset[Purpose]] = {
    DataClass.NAME: frozenset({Purpose.COLLECTIONS, Purpose.SUPPORT, Purpose.MARKETING}),
    DataClass.PHONE: frozenset({Purpose.COLLECTIONS, Purpose.SUPPORT}),
    DataClass.ADDRESS: frozenset({Purpose.COLLECTIONS}),
    DataClass.FINANCIAL: frozenset({Purpose.COLLECTIONS, Purpose.FRAUD_PREVENTION}),
    DataClass.VOICE_RECORDING: frozenset({Purpose.COLLECTIONS, Purpose.FRAUD_PREVENTION}),
    DataClass.TRANSCRIPT: frozenset({Purpose.COLLECTIONS, Purpose.ANALYTICS}),
}


class PurposeRegistry:
    """Which purposes a data class may be processed for."""

    def __init__(self, allowed_purposes: dict[DataClass, frozenset[Purpose]] | None = None) -> None:
        self._allowed = dict(allowed_purposes or _DEFAULT_ALLOWED_PURPOSES)

    def allowed_purposes(self, data_class: DataClass) -> frozenset[Purpose]:
        return self._allowed.get(data_class, frozenset())

    def is_allowed(self, data_class: DataClass, purpose: Purpose) -> bool:
        return purpose in self.allowed_purposes(data_class)
