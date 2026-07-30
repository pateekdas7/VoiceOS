"""SystemXController — governed, auditable incident lifecycle orchestrator.

Full execution flow (Req-1 through Req-11):
    Alert ingest
        → Deduplication (guardrails)
        → Correlation (new incident or append to existing)
        → Record incident + notify start
        → Bidirectional Claude diagnostic session (tool-use loop)
        → Validate Claude response
        → Recovery Policy Engine evaluation
        → Guardrails check (rate limit, cooldown, restart limit)
        → Human approval gate (if required by policy)
        → Recovery Engine (service isolation order, dry-run aware)
        → Health verification
        → Rollback if health worsened
        → Resolve / mark FAILED
        → Resolution notification

Claude is advisory only. The Policy Engine, Validator, and Guardrails make
every operational decision. No Claude output is ever executed directly.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from .classifier import IncidentClassifier
from .claude_client import SystemXClaudeClient
from .correlator import IncidentCorrelator
from .guardrails import RecoveryGuardrails
from .health_verifier import HealthVerifier
from .models import (
    AuditEntry,
    DiagnosticTurn,
    IncidentRecord,
    IncidentSeverity,
    IncidentStatus,
    IngestAlert,
    PolicyLevel,
    ValidationOutcome,
)
from .notifications.engine import NotificationEngine
from .package_builder import IncidentPackageBuilder
from .policy import RecoveryPolicyEngine
from .recovery.engine import RecoveryEngine
from .repositories.audit import SystemXAuditRepository
from .repositories.incident import SystemXIncidentRepository

_log = logging.getLogger("system_x.controller")


class SystemXController:
    """Governed autonomous incident lifecycle orchestrator.

    Thread-safety: each handle_alert() spawns an independent asyncio Task.
    Concurrent incidents run in parallel; each has its own in-flight guard.
    """

    def __init__(
        self,
        incident_repo: SystemXIncidentRepository,
        audit_repo: SystemXAuditRepository,
        classifier: IncidentClassifier,
        correlator: IncidentCorrelator,
        package_builder: IncidentPackageBuilder,
        claude_client: SystemXClaudeClient,
        recovery_engine: RecoveryEngine,
        health_verifier: HealthVerifier,
        notification_engine: NotificationEngine,
        policy_engine: RecoveryPolicyEngine,
        guardrails: RecoveryGuardrails,
    ) -> None:
        self._incident_repo = incident_repo
        self._audit_repo = audit_repo
        self._classifier = classifier
        self._correlator = correlator
        self._package_builder = package_builder
        self._claude_client = claude_client
        self._recovery_engine = recovery_engine
        self._health_verifier = health_verifier
        self._notifier = notification_engine
        self._policy = policy_engine
        self._guardrails = guardrails
        self._in_flight: set[str] = set()

    async def handle_alert(self, alert_payload: dict[str, Any]) -> None:
        """Entry point: called from the Alertmanager webhook handler."""
        alert = self._normalize(alert_payload)
        if alert is None:
            return

        incident_id, is_new = self._correlator.correlate(alert)

        if not is_new:
            self._audit_repo.append(AuditEntry(
                entry_id=str(uuid.uuid4()),
                incident_id=incident_id,
                recorded_at=datetime.now(UTC),
                actor="system_x",
                action=f"correlate_alert:{alert.alert_name}:{alert.fingerprint}",
            ))
            return

        if incident_id in self._in_flight:
            return

        self._in_flight.add(incident_id)
        asyncio.ensure_future(self._run_incident(incident_id))

    # ------------------------------------------------------------------
    # Lifecycle wrapper
    # ------------------------------------------------------------------

    async def _run_incident(self, incident_id: str) -> None:
        try:
            await self._lifecycle(incident_id)
        except Exception:
            _log.exception("incident=%s lifecycle crashed", incident_id)
            try:
                self._incident_repo.update_status(incident_id, IncidentStatus.FAILED)
            except Exception:
                pass
        finally:
            self._in_flight.discard(incident_id)
            self._guardrails.record_complete(incident_id)
            self._correlator.close(incident_id)

    # ------------------------------------------------------------------
    # Full governed lifecycle
    # ------------------------------------------------------------------

    async def _lifecycle(self, incident_id: str) -> None:
        alerts = self._correlator.get_alerts(incident_id)
        if not alerts:
            return

        severity = self._classifier.classify_severity(alerts[0])
        affected_services = self._classifier.classify_services(alerts)
        title = self._classifier.build_title(alerts, severity)
        now = datetime.now(UTC)
        fingerprints = [a.fingerprint for a in alerts]

        # ── Phase 0: Deduplication ─────────────────────────────────────────
        if self._guardrails.is_incident_duplicate(fingerprints):
            _log.info("incident=%s suppressed (duplicate fingerprints)", incident_id)
            return

        # ── Phase 1: Record incident ───────────────────────────────────────
        incident = IncidentRecord(
            incident_id=incident_id,
            title=title,
            severity=severity,
            status=IncidentStatus.DETECTING,
            detected_at=now,
            affected_services=tuple(affected_services),
            alert_fingerprints=tuple(fingerprints),
        )
        self._incident_repo.create(incident)
        self._audit_repo.append(AuditEntry(
            entry_id=str(uuid.uuid4()),
            incident_id=incident_id,
            recorded_at=now,
            actor="system_x",
            action="incident_created",
            result=f"severity={severity} services={','.join(affected_services)}",
        ))
        _log.info("incident=%s created severity=%s", incident_id, severity)

        # ── Phase 2: Notify incident start ────────────────────────────────
        try:
            sent_ids = await self._notifier.notify_incident_start(incident)
            if sent_ids:
                self._incident_repo.update_status(
                    incident_id, IncidentStatus.DETECTING,
                    notifications_sent=list(sent_ids),
                )
        except Exception as exc:
            _log.warning("incident=%s start notification failed: %s", incident_id, exc)

        # ── Phase 3: Bidirectional Claude diagnostic session ──────────────
        self._incident_repo.update_status(incident_id, IncidentStatus.ANALYZING)
        analysis = None
        turns: list[DiagnosticTurn] = []
        try:
            package = self._package_builder.build(incident_id, severity, alerts, affected_services)
            analysis, turns = await self._claude_client.analyze_incident(package)

            self._incident_repo.update_status(
                incident_id, IncidentStatus.ANALYZING,
                claude_analysis=analysis,
            )
            self._audit_repo.append(AuditEntry(
                entry_id=str(uuid.uuid4()),
                incident_id=incident_id,
                recorded_at=datetime.now(UTC),
                actor="system_x:claude",
                action="diagnostic_session_complete",
                result=(
                    f"conversation={analysis.conversation_id} "
                    f"turns={analysis.turn_count} "
                    f"confidence={analysis.confidence} "
                    f"steps={len(analysis.recovery_plan)}"
                ),
                metadata={
                    "evidence_keys": list(analysis.evidence_keys),
                    "turns": [
                        {
                            "turn": t.turn,
                            "tools_called": list(t.tools_called),
                            "summary": t.content_summary,
                        }
                        for t in turns
                    ],
                },
            ))
            _log.info("incident=%s analysis complete confidence=%s turns=%d", incident_id, analysis.confidence, len(turns))

            # Notify analysis complete
            try:
                await self._notifier.notify_analysis_complete(incident, analysis)
            except Exception as exc:
                _log.warning("incident=%s analysis notification failed: %s", incident_id, exc)

        except ValueError as exc:
            # Validation failure — Claude response was rejected
            _log.error("incident=%s Claude validation failed: %s", incident_id, exc)
            self._audit_repo.append(AuditEntry(
                entry_id=str(uuid.uuid4()),
                incident_id=incident_id,
                recorded_at=datetime.now(UTC),
                actor="system_x",
                action="analysis_validation_failed",
                result=str(exc),
            ))
        except Exception as exc:
            _log.error("incident=%s Claude diagnostic session failed: %s", incident_id, exc)
            self._audit_repo.append(AuditEntry(
                entry_id=str(uuid.uuid4()),
                incident_id=incident_id,
                recorded_at=datetime.now(UTC),
                actor="system_x",
                action="diagnostic_session_failed",
                result=str(exc),
            ))

        # ── Phase 4: Recovery Policy Engine ───────────────────────────────
        actions = []
        recovery_summary = "No recovery actions — analysis unavailable or validation failed."

        if analysis is not None:
            policy = self._policy.evaluate(incident, analysis)
            self._audit_repo.append(AuditEntry(
                entry_id=str(uuid.uuid4()),
                incident_id=incident_id,
                recorded_at=datetime.now(UTC),
                actor="system_x.policy",
                action="policy_decision",
                result=f"allowed={policy.allowed} level={policy.policy_level} dry_run={policy.dry_run}",
                metadata={
                    "requires_human": policy.requires_human_approval,
                    "approved_actions": [str(a) for a in policy.approved_actions],
                    "reason": policy.reason,
                },
            ))

            if not policy.allowed:
                _log.warning("incident=%s recovery blocked by policy: %s", incident_id, policy.reason)
                recovery_summary = f"Recovery blocked by policy: {policy.reason}"
                try:
                    await self._notifier.notify_approval_required(incident, policy.reason)
                except Exception as exc:
                    _log.warning("incident=%s approval notification failed: %s", incident_id, exc)
            else:
                # ── Phase 5: Guardrails check ───────────────────────────
                guard_ok, guard_reason = self._guardrails.check_can_recover(
                    incident_id, affected_services, fingerprints
                )
                self._audit_repo.append(AuditEntry(
                    entry_id=str(uuid.uuid4()),
                    incident_id=incident_id,
                    recorded_at=datetime.now(UTC),
                    actor="system_x.guardrails",
                    action="guardrails_check",
                    result=f"allowed={guard_ok} reason={guard_reason}",
                ))

                if not guard_ok:
                    _log.warning("incident=%s guardrails blocked: %s", incident_id, guard_reason)
                    recovery_summary = f"Recovery blocked by guardrails: {guard_reason}"
                else:
                    # ── Phase 6: Human approval gate ───────────────────
                    if policy.requires_human_approval and not policy.dry_run:
                        _log.info("incident=%s awaiting human approval", incident_id)
                        self._incident_repo.update_status(incident_id, IncidentStatus.AWAITING_APPROVAL)
                        try:
                            await self._notifier.notify_approval_required(incident, policy.reason)
                        except Exception as exc:
                            _log.warning("incident=%s approval notification failed: %s", incident_id, exc)
                        # In this implementation, AWAITING_APPROVAL is a terminal state
                        # that an operator resolves via the admin UI. The lifecycle ends here
                        # for high-risk incidents — the operator approves manually.
                        self._audit_repo.append(AuditEntry(
                            entry_id=str(uuid.uuid4()),
                            incident_id=incident_id,
                            recorded_at=datetime.now(UTC),
                            actor="system_x",
                            action="human_approval_required",
                            result="lifecycle paused — operator must approve via admin UI",
                        ))
                        return

                    # ── Phase 7: Recovery execution ─────────────────────
                    self._incident_repo.update_status(incident_id, IncidentStatus.RECOVERING)
                    self._guardrails.record_start(incident_id, affected_services, fingerprints)

                    try:
                        actions = await self._recovery_engine.execute_recovery_plan(
                            incident_id, analysis, affected_services, policy
                        )
                    except Exception as exc:
                        _log.error("incident=%s recovery execution failed: %s", incident_id, exc)
                        self._audit_repo.append(AuditEntry(
                            entry_id=str(uuid.uuid4()),
                            incident_id=incident_id,
                            recorded_at=datetime.now(UTC),
                            actor="system_x",
                            action="recovery_execution_failed",
                            result=str(exc),
                        ))

                    recovery_summary = self._recovery_engine.build_recovery_summary(actions)

        # ── Phase 8: Health verification ──────────────────────────────────
        self._incident_repo.update_status(incident_id, IncidentStatus.VERIFYING)
        health_snapshot: dict[str, object] = {}
        all_ok = False

        try:
            health_snapshot = await self._health_verifier.verify(affected_services)
            all_ok = await self._health_verifier.all_healthy(health_snapshot)
        except Exception as exc:
            _log.error("incident=%s health verification failed: %s", incident_id, exc)

        self._audit_repo.append(AuditEntry(
            entry_id=str(uuid.uuid4()),
            incident_id=incident_id,
            recorded_at=datetime.now(UTC),
            actor="system_x",
            action="health_verification",
            verification_outcome="healthy" if all_ok else "degraded",
            metadata=health_snapshot,
        ))

        # ── Phase 9: Rollback if health worsened ─────────────────────────
        if not all_ok and actions and analysis is not None:
            # Check for timeout breach too
            timed_out, timeout_msg = self._guardrails.check_timeout(incident_id)
            rollback_reason = timeout_msg if timed_out else "health verification failed after recovery"

            _log.warning("incident=%s initiating rollback: %s", incident_id, rollback_reason)
            self._incident_repo.update_status(incident_id, IncidentStatus.ROLLING_BACK)

            try:
                await self._recovery_engine.rollback(incident_id, actions, rollback_reason)
            except Exception as exc:
                _log.error("incident=%s rollback failed: %s", incident_id, exc)

            recovery_summary = f"[ROLLED BACK] {rollback_reason}. Original: {recovery_summary}"

            # Re-verify after rollback
            try:
                health_snapshot = await self._health_verifier.verify(affected_services)
                all_ok = await self._health_verifier.all_healthy(health_snapshot)
            except Exception:
                all_ok = False

            self._audit_repo.append(AuditEntry(
                entry_id=str(uuid.uuid4()),
                incident_id=incident_id,
                recorded_at=datetime.now(UTC),
                actor="system_x",
                action="post_rollback_verification",
                verification_outcome="healthy" if all_ok else "still_degraded",
            ))

        # ── Phase 10: Resolve ─────────────────────────────────────────────
        resolved_at = datetime.now(UTC)
        downtime_s = int((resolved_at - now).total_seconds())
        final_status = IncidentStatus.RESOLVED if all_ok else IncidentStatus.FAILED
        root_cause = analysis.root_cause if analysis else "Analysis unavailable"

        self._incident_repo.update_status(
            incident_id,
            final_status,
            resolved_at=resolved_at,
            root_cause=root_cause,
            recovery_summary=recovery_summary,
            health_after=health_snapshot,
            total_downtime_s=downtime_s,
        )
        self._audit_repo.append(AuditEntry(
            entry_id=str(uuid.uuid4()),
            incident_id=incident_id,
            recorded_at=resolved_at,
            actor="system_x",
            action="incident_closed",
            result=str(final_status),
        ))
        _log.info("incident=%s status=%s downtime=%ds", incident_id, final_status, downtime_s)

        # ── Phase 11: Resolution notification ────────────────────────────
        final_incident = self._incident_repo.get(incident_id)
        if final_incident:
            try:
                await self._notifier.notify_resolved(final_incident)
            except Exception as exc:
                _log.warning("incident=%s resolved notification failed: %s", incident_id, exc)

    # ------------------------------------------------------------------
    # Alert normalization
    # ------------------------------------------------------------------

    def _normalize(self, payload: dict[str, Any]) -> IngestAlert | None:
        try:
            labels = dict(payload.get("labels", {}))
            annotations = dict(payload.get("annotations", {}))
            name = labels.get("alertname", payload.get("alert_name", "unknown"))
            severity = labels.get("severity", payload.get("severity", "warning"))
            service = labels.get("service", labels.get("job", "platform"))
            fingerprint = payload.get("fingerprint") or f"{name}:{service}"
            fired_at_raw = payload.get("startsAt") or payload.get("fired_at")
            try:
                fired_at = datetime.fromisoformat(str(fired_at_raw).replace("Z", "+00:00"))
            except Exception:
                fired_at = datetime.now(UTC)
            return IngestAlert(
                fingerprint=fingerprint,
                alert_name=name,
                severity=severity,
                service=service,
                labels=labels,
                annotations=annotations,
                fired_at=fired_at,
            )
        except Exception as exc:
            _log.warning("failed to normalize alert payload: %s", exc)
            return None


__all__ = ["SystemXController"]
