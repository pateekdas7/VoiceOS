"""Audit Architecture — append-only, hash-chained, tamper-evident trail (V4 Ch11).

Architecture: V4 Ch11 (Audit Architecture).
"""

from __future__ import annotations

from src.libs.audit.event import AuditEvent, AuditEventType
from src.libs.audit.logger import AuditLogger
from src.libs.audit.search import AuditSearch
from src.libs.audit.verifier import AuditVerifier, VerificationResult

__all__ = [
    "AuditEvent",
    "AuditEventType",
    "AuditLogger",
    "AuditSearch",
    "AuditVerifier",
    "VerificationResult",
]
