"""ComplianceMonitoring — real-time compliance signal correlation + alerting (V4 Ch16).

In-process library façade, consistent with every VoiceOS service since
Sprint-013 (no standalone HTTP/gRPC listener ships before Sprint-026 —
see ``src/services/policy_engine/service.py``'s docstring for the
established precedent).

Architecture: V4 Ch16 (Compliance Monitoring) §16.7 (Public Interfaces).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from src.libs.audit.event import AuditEvent

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
    ) -> None:
        self._rule_set = rule_set or ComplianceRuleSet()
        self._correlator = correlator or SignalCorrelator(self._rule_set.rules())
        self._alerter = alerter or ComplianceAlerter()
        self._violated_tenants: set[str] = set()

    @classmethod
    def create(cls, publisher: Any | None = None, policy_engine_service: Any | None = None) -> ComplianceMonitoring:
        """Factory: build a ComplianceMonitoring with the given (optional) backends."""
        rule_set = ComplianceRuleSet(policy_engine_service=policy_engine_service)
        return cls(
            rule_set=rule_set, correlator=SignalCorrelator(rule_set.rules()), alerter=ComplianceAlerter(publisher)
        )

    def ingest(self, audit_event: AuditEvent) -> None:
        """Process one audit event in real time (V4 Ch16 §16.7)."""
        signal = self._correlator.correlate(audit_event)
        if signal is not None:
            self._violated_tenants.add(signal.tenant_id)
            self._alerter.alert(signal)

    def status(self, tenant_id: str) -> ComplianceStatus:
        """Current compliance posture for ``tenant_id`` (V4 Ch16 §16.7)."""
        return ComplianceStatus.VIOLATION if tenant_id in self._violated_tenants else ComplianceStatus.COMPLIANT

    def rules(self) -> tuple[Any, ...]:
        """The active monitor rules (V4 Ch16 §16.7)."""
        return self._rule_set.rules()
