"""SignalCorrelator — correlates audit events into compliance signals (V4 Ch16 §16.12).

A sliding-window count per (tenant, rule): each matching audit event is
timestamped into that rule's window; crossing ``threshold_count`` within
``window_seconds`` raises one :class:`ComplianceSignal` and resets the
window (so a sustained pattern alerts repeatedly rather than once, but a
single burst doesn't re-alert on every subsequent matching event).

Architecture: V4 Ch16 (Compliance Monitoring) §16.9 (Data Flow), §16.12.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from src.libs.audit.event import AuditEvent

from .rules import MonitorRule


@dataclass(frozen=True)
class ComplianceSignal:
    """A raised compliance signal — a rule's threshold was crossed (V4 Ch16 §16.6)."""

    tenant_id: str
    rule_id: str
    alert_kind: str
    matched_count: int


class SignalCorrelator:
    """Correlates a stream of :class:`AuditEvent` into :class:`ComplianceSignal`."""

    def __init__(self, rules: tuple[MonitorRule, ...]) -> None:
        self._rules = rules
        self._windows: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def correlate(self, audit_event: AuditEvent, *, now: float | None = None) -> ComplianceSignal | None:
        """Ingest one audit event; return a signal if it crosses a rule's threshold."""
        current_time = now if now is not None else time.time()

        for rule in self._rules:
            if audit_event.action != rule.matches_action:
                continue

            key = (audit_event.tenant_id, rule.rule_id)
            window = self._windows[key]
            window.append(current_time)

            cutoff = current_time - rule.window_seconds
            while window and window[0] < cutoff:
                window.popleft()

            if len(window) >= rule.threshold_count:
                matched_count = len(window)
                window.clear()
                return ComplianceSignal(
                    tenant_id=audit_event.tenant_id,
                    rule_id=rule.rule_id,
                    alert_kind=rule.alert_kind,
                    matched_count=matched_count,
                )

        return None
