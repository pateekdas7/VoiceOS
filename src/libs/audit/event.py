"""AuditEvent — the typed audit record shape (V4 Ch11 §11.6).

Mirrors the ``audit_log`` table (Sprint-014 migration 0010; Sprint-020
migration 0017 adds the hash-chain columns). Used by :class:`AuditSearch`
and :class:`~src.libs.audit.verifier.AuditVerifier` as the typed read shape;
:class:`~src.libs.audit.logger.AuditLogger` is the write path.

Architecture: V4 Ch11 (Audit Architecture) §11.6 (Outputs).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditEventType(StrEnum):
    """Audit event category (V4 Ch11 §11.6)."""

    AI_DECISION = "AI_DECISION"
    POLICY = "POLICY"
    AUTHN = "AUTHN"
    AUTHZ = "AUTHZ"
    ADMIN = "ADMIN"
    DATA_ACCESS = "DATA_ACCESS"
    CONFIG = "CONFIG"


# Stable action codes for the mandatory-coverage events (Sprint-020.md
# "Required audit events"; V4 Ch11 §11.12 "Coverage policy").
ACTION_AUTHN_LOGIN = "authn.login"
ACTION_AUTHN_LOGOUT = "authn.logout"
ACTION_AUTHN_FAILED = "authn.failed"
ACTION_POLICY_DENIED = "policy.denied"
ACTION_DATA_ACCESS_PII_READ = "data_access.pii_read"
ACTION_PTP_CREATED = "ptp.created"
ACTION_CONSENT_GRANTED = "consent.granted"
ACTION_CONSENT_REVOKED = "consent.revoked"
ACTION_AI_GOVERNANCE_BLOCK = "ai_governance.block"
ACTION_AI_GOVERNANCE_REQUIRE_HUMAN = "ai_governance.require_human"
ACTION_DATA_ERASURE = "data.erasure"
ACTION_API_KEY_ISSUED = "api_key.issued"
ACTION_API_KEY_REVOKED = "api_key.revoked"
ACTION_TENANT_LIFECYCLE_TRANSITION = "tenant.lifecycle_transition"
ACTION_TENANT_PROVISIONED = "tenant.provisioned"
ACTION_USER_CREATED = "user.created"
ACTION_USER_INVITED = "user.invited"
ACTION_USER_ACTIVATED = "user.activated"
ACTION_HITL_DECISION = "hitl.decision_recorded"


class AuditEvent(BaseModel):
    """Read-side representation of one ``audit_log`` row."""

    model_config = ConfigDict(frozen=True)

    audit_id: str
    tenant_id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    outcome: str
    ip_address: str = ""
    event_payload: dict[str, Any] | None = None
    recorded_at: datetime
    seq: int | None = None
    prev_hash: str | None = None
    hash: str | None = None
