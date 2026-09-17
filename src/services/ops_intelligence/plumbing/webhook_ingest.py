"""WebhookIngestService -- adapts the two existing alert sources into AlertLifecycleService.

Receives (1) Alertmanager's own webhook_configs POST body, and (2) the
``compliance.violation_alert`` event ``ComplianceAlerter`` already publishes
onto the ``voiceos-events`` bus -- and turns each into exactly one
``AlertLifecycleService.ingest()`` call. Neither source's own detection
logic (Prometheus alert rules; ``compliance_monitoring.correlator``'s
signal correlation) is touched or reimplemented here (ADR-006 Sec 4/13.4).
"""

from __future__ import annotations

from typing import Any

from src.services.ops_intelligence.models import AlertSource, Severity

from .alert_lifecycle import AlertLifecycleService, make_fingerprint

_ALERTMANAGER_SEVERITY_MAP = {"critical": Severity.CRITICAL, "warning": Severity.WARNING, "info": Severity.INFO}

# compliance_monitoring signal_type -> Severity. All three of ComplianceAlerter's
# default rules (COMPLIANCE_SIGNAL_CONSENT_BYPASS_ATTEMPT,
# COMPLIANCE_SIGNAL_REPEATED_POLICY_DENIAL, COMPLIANCE_SIGNAL_REPEATED_AUTHN_FAILURE)
# are short-fuse security signals -- treated as CRITICAL by default; a real
# deployment may refine this per signal_type without changing this adapter's shape.
_DEFAULT_COMPLIANCE_SEVERITY = Severity.CRITICAL


class WebhookIngestService:
    """Normalizes both existing alert sources into ``AlertLifecycleService.ingest()``."""

    def __init__(self, lifecycle: AlertLifecycleService) -> None:
        self._lifecycle = lifecycle

    def ingest_alertmanager_webhook(self, payload: dict[str, Any]) -> tuple[Any, ...]:
        """Handle one Alertmanager webhook POST body (may batch multiple alerts)."""
        results = []
        for raw_alert in payload.get("alerts", ()):
            labels = dict(raw_alert.get("labels", {}))
            annotations = dict(raw_alert.get("annotations", {}))
            name = labels.get("alertname", "unknown")
            severity = _ALERTMANAGER_SEVERITY_MAP.get(labels.get("severity", "warning"), Severity.WARNING)
            tenant_id = labels.get("tenant_id")
            fingerprint = raw_alert.get("fingerprint") or make_fingerprint(
                source=AlertSource.ALERTMANAGER, name=name, labels=labels
            )
            results.append(
                self._lifecycle.ingest(
                    source=AlertSource.ALERTMANAGER,
                    fingerprint=fingerprint,
                    severity=severity,
                    tenant_id=tenant_id,
                    labels=labels,
                    annotations=annotations,
                )
            )
        return tuple(results)

    def ingest_compliance_signal(self, event_payload: dict[str, Any]) -> Any:
        """Handle one ``compliance.violation_alert`` event-bus payload.

        ``event_payload`` is the full serialized domain event (matching the
        convention every EventBus consumer handler in this repo uses --
        see ``metering.collector.UsageCollector``'s handlers).
        """
        signal_type = str(event_payload.get("signal_type", "unknown"))
        tenant_id = event_payload.get("tenant_id")
        labels = {"signal_type": signal_type}
        fingerprint = make_fingerprint(source=AlertSource.COMPLIANCE_MONITORING, name=signal_type, labels=labels)
        return self._lifecycle.ingest(
            source=AlertSource.COMPLIANCE_MONITORING,
            fingerprint=fingerprint,
            severity=_DEFAULT_COMPLIANCE_SEVERITY,
            tenant_id=tenant_id,
            labels=labels,
            annotations={"description": str(event_payload.get("description", ""))},
        )


__all__ = ["WebhookIngestService"]
