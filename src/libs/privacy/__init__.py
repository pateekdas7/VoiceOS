"""Privacy architecture — minimization, purpose limitation, right-to-erasure, retention (V4 Ch9).

Architecture: V4 Ch9 (Privacy Architecture).
"""

from __future__ import annotations

from src.libs.privacy.engine import PrivacyDecision, PrivacyEngine, PrivacyObligation
from src.libs.privacy.erasure import (
    ConsentNotRevokedError,
    DataErasureCertificate,
    DataErasureJob,
    ErasureResult,
)
from src.libs.privacy.minimizer import DataMinimizer
from src.libs.privacy.object_store import LocalDiskObjectStore
from src.libs.privacy.purpose_registry import DataClass, Purpose, PurposeRegistry
from src.libs.privacy.retention import RetentionScheduler

__all__ = [
    "ConsentNotRevokedError",
    "DataClass",
    "DataErasureCertificate",
    "DataErasureJob",
    "DataMinimizer",
    "ErasureResult",
    "LocalDiskObjectStore",
    "PrivacyDecision",
    "PrivacyEngine",
    "PrivacyObligation",
    "Purpose",
    "PurposeRegistry",
    "RetentionScheduler",
]
