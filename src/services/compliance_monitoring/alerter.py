"""ComplianceAlerter — emits ComplianceViolationAlert events to the EventBus (V4 Ch16).

Architecture: V4 Ch16 (Compliance Monitoring) §16.6 (``ComplianceAlert``),
§16.9 (feeds Ch17 Incident Response + Ch18 Governance Dashboards).
"""

from __future__ import annotations

from typing import Any

from .correlator import ComplianceSignal

COMPLIANCE_VIOLATION_ALERT_EVENT_TYPE = "compliance.violation_alert"


class ComplianceAlerter:
    """Emits ``ComplianceViolationAlert`` events for raised :class:`ComplianceSignal`."""

    def __init__(self, publisher: Any | None = None) -> None:
        self._publisher = publisher
        self._alerts: list[ComplianceSignal] = []

    def alert(self, signal: ComplianceSignal) -> None:
        """Record and (if wired) publish a compliance violation alert."""
        self._alerts.append(signal)

        if self._publisher is not None:
            self._publisher.publish(
                event_type=COMPLIANCE_VIOLATION_ALERT_EVENT_TYPE,
                tenant_id=signal.tenant_id,
                payload={
                    "rule_id": signal.rule_id,
                    "alert_kind": signal.alert_kind,
                    "matched_count": signal.matched_count,
                },
                correlation_id=signal.rule_id,
            )

    @property
    def alerts(self) -> tuple[ComplianceSignal, ...]:
        """Every alert raised so far (test/dashboard observability)."""
        return tuple(self._alerts)
