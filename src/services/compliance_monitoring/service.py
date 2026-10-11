"""ComplianceMonitoring — real-time compliance signal correlation + alerting (V4 Ch16).

In-process library façade, consistent with every VoiceOS service since
Sprint-013 (no standalone HTTP/gRPC listener ships before Sprint-026 —
see ``src/services/policy_engine/service.py``'s docstring for the
established precedent).

Phase 6d wires an optional ``ComplianceViolationRepository`` so that
violation state survives service restarts. When the repository is None
(existing tests, local dev without a DB) the original in-memory
``_violated_tenants`` set is used as fallback — all existing callers and
tests remain unmodified.

Architecture: V4 Ch16 (Compliance Monitoring) §16.7 (Public Interfaces).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from src.libs.audit.event import AuditEvent
from src.libs.contracts.primitives import TenantId

from .alerter import ComplianceAlerter
from .correlator import SignalCorrelator
from .rules import ComplianceRuleSet


class ComplianceStatus(StrEnum):
    """A tenant's current compliance posture (V4 Ch16 §16.7 ``status()``)."""

    COMPLIANT = "COMPLIANT"
    VIOLATION = "VIOLATION"


class ComplianceMonitoring:
    """Subscribes to the audit event stream and raises compliance alerts in real time."""

    def __init__(
        self,
        rule_set: ComplianceRuleSet | None = None,
        correlator: SignalCorrelator | None = None,
        alerter: ComplianceAlerter | None = None,
        violation_repository: Any | None = None,
    ) -> None:
        self._rule_set = rule_set or ComplianceRuleSet()
        self._correlator = correlator or SignalCorrelator(self._rule_set.rules())
        self._alerter = alerter or ComplianceAlerter()
        self._violated_tenants: set[str] = set()
        self._violation_repository = violation_repository

    @classmethod
    def create(
        cls,
        publisher: Any | None = None,
        policy_engine_service: Any | None = None,
        violation_repository: Any | None = None,
    ) -> ComplianceMonitoring:
        """Factory: build a ComplianceMonitoring with the given (optional) backends."""
        rule_set = ComplianceRuleSet(policy_engine_service=policy_engine_service)
        return cls(
            rule_set=rule_set,
            correlator=SignalCorrelator(rule_set.rules()),
            alerter=ComplianceAlerter(publisher),
            violation_repository=violation_repository,
        )

    def ingest(self, audit_event: AuditEvent) -> None:
        """Process one audit event in real time (V4 Ch16 §16.7)."""
        signal = self._correlator.correlate(audit_event)
        if signal is not None:
            self._violated_tenants.add(signal.tenant_id)
            if self._violation_repository is not None:
                self._violation_repository.upsert_active(
                    TenantId(signal.tenant_id),
                    signal.rule_id,
                    f"{signal.alert_kind}: {signal.matched_count} events correlated",
                )
            self._alerter.alert(signal)

    def status(self, tenant_id: str) -> ComplianceStatus:
        """Current compliance posture for ``tenant_id`` (V4 Ch16 §16.7)."""
        if self._violation_repository is not None:
            return (
                ComplianceStatus.VIOLATION
                if self._violation_repository.is_violated(TenantId(tenant_id))
                else ComplianceStatus.COMPLIANT
            )
        return ComplianceStatus.VIOLATION if tenant_id in self._violated_tenants else ComplianceStatus.COMPLIANT

    def rules(self) -> tuple[Any, ...]:
        """The active monitor rules (V4 Ch16 §16.7)."""
        return self._rule_set.rules()
