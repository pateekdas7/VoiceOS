"""AlertLifecycleService -- the single, deterministic alert state machine (ADR-006 Sec 4).

Reconciles the two independent, pre-existing alert-producing mechanisms
(Prometheus/Alertmanager threshold rules, and
``compliance_monitoring.ComplianceAlerter``'s event-bus-published signals)
into ONE lifecycle model, written once (ADR-006 Sec 13.4 -- "no duplicated
alerting logic"): ``webhook_ingest.py`` adapts both sources into
:meth:`AlertLifecycleService.ingest`; this class never re-implements either
source's own detection/correlation logic.

Zero dependency on the reasoning model or any LLM adapter (ADR-006 Sec 3.0)
-- this is the always-on half of ``ops_intelligence``. Escalation delivery
reuses the existing Alertmanager routing (PagerDuty/Slack/Jira, unchanged)
for ``alertmanager``-sourced alerts, and the new ``notifications`` service
(ADR-005 Sec 4.5) for in-app/email/dashboard notifications, for both sources.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from src.services.ops_intelligence.models import AlertRecord, AlertSource, AlertStatus, Severity

from .metrics import record_open_alerts, red_metrics
from .repository import AlertRepositoryPort

# Per-severity escalation timeout: how long a FIRING alert may go
# unacknowledged before it is automatically escalated (ADR-006 Sec 4).
DEFAULT_ESCALATION_TIMEOUTS_SECONDS: dict[Severity, int] = {
    Severity.CRITICAL: 15 * 60,
    Severity.WARNING: 60 * 60,
    Severity.INFO: 24 * 60 * 60,
}


class AlertNotificationPort(Protocol):
    """Injected delivery port -- in-app/email/dashboard (ADR-005 Sec 4.5 ``notifications`` service).

    Alertmanager's own PagerDuty/Slack/Jira routing is unchanged and is NOT
    re-implemented behind this port; this port is exclusively the new
    channel(s) Alertmanager itself never had (ADR-006 Sec 4).
    """

    def notify_alert(self, alert: AlertRecord) -> None: ...


class NullAlertNotificationPort:
    """No-op notification port -- used when no delivery channel is wired yet."""

    def notify_alert(self, alert: AlertRecord) -> None:
        return None


class AlertLifecycleService:
    """FIRING -> ACKNOWLEDGED -> RESOLVED (or -> ESCALATED -> ACKNOWLEDGED -> RESOLVED)."""

    def __init__(
        self,
        repository: AlertRepositoryPort,
        notification_port: AlertNotificationPort | None = None,
        *,
        escalation_timeouts: dict[Severity, int] = DEFAULT_ESCALATION_TIMEOUTS_SECONDS,
        clock: object = time,
    ) -> None:
        self._repository = repository
        self._notification_port = notification_port or NullAlertNotificationPort()
        self._escalation_timeouts = escalation_timeouts
        self._clock = clock

    def ingest(
        self,
        *,
        source: AlertSource,
        fingerprint: str,
        severity: Severity,
        tenant_id: str | None,
        labels: dict[str, str],
        annotations: dict[str, str],
    ) -> AlertRecord:
        """Record a new alert firing, or return the existing open alert for this fingerprint.

        Mirrors Alertmanager's own re-fire semantics: a repeated firing of an
        already-open alert (same fingerprint) does not create a second row.
        """
        existing = self._repository.find_by_fingerprint_open(fingerprint)
        if existing is not None:
            return existing

        alert = AlertRecord(
            alert_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            source=source,
            fingerprint=fingerprint,
            severity=severity,
            status=AlertStatus.FIRING,
            fired_at=datetime.now(UTC),
            labels=dict(labels),
            annotations=dict(annotations),
        )
        created = self._repository.create(alert)
        red_metrics.record_request("created", 0.0)
        self._notification_port.notify_alert(created)
        self._refresh_open_alert_gauge()
        return created

    def acknowledge(self, alert_id: str, *, by: str) -> AlertRecord:
        alert = self._require(alert_id)
        updated = replace(alert, status=AlertStatus.ACKNOWLEDGED, acknowledged_at=datetime.now(UTC), acknowledged_by=by)
        result = self._repository.update_status(alert_id, updated)
        self._refresh_open_alert_gauge()
        return result

    def escalate(self, alert_id: str, *, to: str) -> AlertRecord:
        alert = self._require(alert_id)
        updated = replace(alert, status=AlertStatus.ESCALATED, escalated_at=datetime.now(UTC), escalated_to=to)
        result = self._repository.update_status(alert_id, updated)
        self._notification_port.notify_alert(result)
        self._refresh_open_alert_gauge()
        return result

    def resolve(self, alert_id: str) -> AlertRecord:
        alert = self._require(alert_id)
        updated = replace(alert, status=AlertStatus.RESOLVED, resolved_at=datetime.now(UTC))
        result = self._repository.update_status(alert_id, updated)
        self._refresh_open_alert_gauge()
        return result

    def evaluate_escalations(self, *, now: datetime | None = None) -> tuple[AlertRecord, ...]:
        """Escalate every FIRING alert that has exceeded its severity's timeout, unacknowledged.

        Intended to run on a scheduled worker-pool task (ADR-006 Sec 4) --
        plain deterministic code, no LLM involvement.
        """
        current_time = now or datetime.now(UTC)
        escalated: list[AlertRecord] = []
        for alert in self._repository.list_open():
            if alert.status != AlertStatus.FIRING:
                continue
            timeout_seconds = self._escalation_timeouts.get(alert.severity, DEFAULT_ESCALATION_TIMEOUTS_SECONDS[Severity.INFO])
            age_seconds = (current_time - alert.fired_at).total_seconds()
            if age_seconds >= timeout_seconds:
                escalated.append(self.escalate(alert.alert_id, to=_next_escalation_tier(alert.severity)))
        return tuple(escalated)

    def _require(self, alert_id: str) -> AlertRecord:
        alert = self._repository.get(alert_id)
        if alert is None:
            raise ValueError(f"no such alert: {alert_id}")
        return alert

    def _refresh_open_alert_gauge(self) -> None:
        for (status, source), count in self._repository.count_by_status().items():
            record_open_alerts(status.value, source, count)


def _next_escalation_tier(severity: Severity) -> str:
    """Escalation target by severity -- config, not code, in a real deployment."""
    return {
        Severity.CRITICAL: "oncall_secondary",
        Severity.WARNING: "oncall_primary",
        Severity.INFO: "team_channel",
    }[severity]


def make_fingerprint(*, source: AlertSource, name: str, labels: dict[str, str]) -> str:
    """Deterministic fingerprint for dedup -- same inputs always produce the same hash."""
    label_part = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
    raw = f"{source.value}:{name}:{label_part}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


__all__ = [
    "DEFAULT_ESCALATION_TIMEOUTS_SECONDS",
    "AlertLifecycleService",
    "AlertNotificationPort",
    "NullAlertNotificationPort",
    "make_fingerprint",
]
