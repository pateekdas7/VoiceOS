"""AuditLogger — append-only, tamper-evident, PII-redacting audit writer.

The PII-redaction and required-event-coverage policy sits here, one layer
above ``AuditRepository`` (V4 Ch11 §11.18 "must not contain raw PII
unnecessarily... audit records the action, not the PII itself" /
Sprint-020.md "PII is redacted in audit log values"). Hash-chaining itself
lives in ``AuditRepository.append()`` (the single INSERT path) — see that
module's docstring for the rationale.

Architecture: V4 Ch11 (Audit Architecture) §11.7 (Public Interfaces), §11.12,
§11.18.
"""

from __future__ import annotations

from typing import Any

from src.libs.pii.redactor import PIIRedactor

from .event import (
    ACTION_AI_GOVERNANCE_BLOCK,
    ACTION_AI_GOVERNANCE_REQUIRE_HUMAN,
    ACTION_API_KEY_ISSUED,
    ACTION_API_KEY_REVOKED,
    ACTION_AUTHN_FAILED,
    ACTION_AUTHN_LOGIN,
    ACTION_AUTHN_LOGOUT,
    ACTION_CONSENT_GRANTED,
    ACTION_CONSENT_REVOKED,
    ACTION_DATA_ACCESS_PII_READ,
    ACTION_DATA_ERASURE,
    ACTION_HITL_DECISION,
    ACTION_POLICY_DENIED,
    ACTION_PTP_CREATED,
    ACTION_TENANT_LIFECYCLE_TRANSITION,
    ACTION_TENANT_PROVISIONED,
    ACTION_USER_ACTIVATED,
    ACTION_USER_CREATED,
    ACTION_USER_INVITED,
)


class AuditLogger:
    """The mandatory entry point for recording an auditable action.

    Args:
        audit_repository: A :class:`~src.libs.repositories.audit.AuditRepository`.
        redactor: Optional :class:`PIIRedactor`; defaults to a fresh instance.
    """

    def __init__(self, audit_repository: Any, redactor: PIIRedactor | None = None) -> None:
        self._repo = audit_repository
        self._redactor = redactor or PIIRedactor()

    def record(
        self,
        tenant_id: Any,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        *,
        event_payload: dict[str, Any] | None = None,
        ip_address: str = "",
    ) -> str:
        """Append one audit record with PII redacted from ``event_payload``.

        Returns the row's chain ``hash`` (from ``AuditRepository.append()``).
        """
        return str(
            self._repo.append(
                tenant_id,
                actor_id,
                action,
                resource_type,
                resource_id,
                outcome,
                event_payload=self._redact_payload(event_payload),
                ip_address=ip_address,
            )
        )

    def _redact_payload(self, payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if payload is None:
            return None
        return {
            key: (self._redactor.redact(value) if isinstance(value, str) else value) for key, value in payload.items()
        }

    # ------------------------------------------------------------------
    # Convenience wrappers for the mandatory-coverage event types
    # (Sprint-020.md "Required audit events"; V4 Ch11 §11.12).
    # ------------------------------------------------------------------

    def record_authn(self, tenant_id: Any, actor_id: str, outcome: str, *, failed: bool = False) -> str:
        action = ACTION_AUTHN_FAILED if failed else ACTION_AUTHN_LOGIN
        return self.record(tenant_id, actor_id, action, "AuthSession", actor_id, outcome)

    def record_authn_logout(self, tenant_id: Any, actor_id: str) -> str:
        return self.record(tenant_id, actor_id, ACTION_AUTHN_LOGOUT, "AuthSession", actor_id, "SUCCESS")

    def record_policy_denied(self, tenant_id: Any, actor_id: str, rule_id: str, explanation: str) -> str:
        return self.record(
            tenant_id,
            actor_id,
            ACTION_POLICY_DENIED,
            "PolicyRule",
            rule_id,
            "DENY",
            event_payload={"explanation": explanation},
        )

    def record_pii_access(self, tenant_id: Any, actor_id: str, resource_type: str, resource_id: str) -> str:
        return self.record(tenant_id, actor_id, ACTION_DATA_ACCESS_PII_READ, resource_type, resource_id, "SUCCESS")

    def record_ptp_created(self, tenant_id: Any, actor_id: str, ptp_id: str) -> str:
        return self.record(tenant_id, actor_id, ACTION_PTP_CREATED, "PromiseToPay", ptp_id, "SUCCESS")

    def record_consent_change(self, tenant_id: Any, actor_id: str, customer_id: str, *, granted: bool) -> str:
        action = ACTION_CONSENT_GRANTED if granted else ACTION_CONSENT_REVOKED
        return self.record(tenant_id, actor_id, action, "Consent", customer_id, "SUCCESS")

    def record_ai_governance_verdict(
        self, tenant_id: Any, call_id: str, *, require_human: bool, explanation: str
    ) -> str:
        action = ACTION_AI_GOVERNANCE_REQUIRE_HUMAN if require_human else ACTION_AI_GOVERNANCE_BLOCK
        return self.record(
            tenant_id,
            "ai_governance_service",
            action,
            "Call",
            call_id,
            "REQUIRE_HUMAN" if require_human else "BLOCK",
            event_payload={"explanation": explanation},
        )

    def record_data_erasure(self, tenant_id: Any, actor_id: str, customer_id: str) -> str:
        return self.record(tenant_id, actor_id, ACTION_DATA_ERASURE, "Customer", customer_id, "SUCCESS")

    def record_api_key_operation(self, tenant_id: Any, actor_id: str, key_id: str, *, revoked: bool = False) -> str:
        action = ACTION_API_KEY_REVOKED if revoked else ACTION_API_KEY_ISSUED
        return self.record(tenant_id, actor_id, action, "APIKey", key_id, "SUCCESS")

    def record_tenant_lifecycle(
        self, tenant_id: Any, actor_id: str, transition: str, *, outcome: str = "SUCCESS"
    ) -> str:
        return self.record(
            tenant_id,
            actor_id,
            ACTION_TENANT_LIFECYCLE_TRANSITION,
            "Tenant",
            str(tenant_id),
            outcome,
            event_payload={"transition": transition},
        )

    def record_tenant_provisioned(self, tenant_id: Any, actor_id: str) -> str:
        return self.record(tenant_id, actor_id, ACTION_TENANT_PROVISIONED, "Tenant", str(tenant_id), "SUCCESS")

    def record_user_created(self, tenant_id: Any, actor_id: str, user_id: str) -> str:
        return self.record(tenant_id, actor_id, ACTION_USER_CREATED, "User", user_id, "SUCCESS")

    def record_user_invited(self, tenant_id: Any, actor_id: str, invitation_id: str) -> str:
        return self.record(tenant_id, actor_id, ACTION_USER_INVITED, "Invitation", invitation_id, "SUCCESS")

    def record_user_activated(self, tenant_id: Any, user_id: str, invitation_id: str) -> str:
        return self.record(tenant_id, user_id, ACTION_USER_ACTIVATED, "Invitation", invitation_id, "SUCCESS")

    def record_hitl_decision(
        self, tenant_id: Any, supervisor_id: str, hitl_item_id: str, decision: str, rationale: str
    ) -> str:
        """Record a supervisor's HITL decision — every override is audited (V4 Ch15 §15.12/§15.17)."""
        return self.record(
            tenant_id,
            supervisor_id,
            ACTION_HITL_DECISION,
            "HITLItem",
            hitl_item_id,
            "SUCCESS",
            event_payload={"decision": decision, "rationale": rationale},
        )
