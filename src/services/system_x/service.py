"""SystemXService — production factory for the Autonomous Operations Controller.

All configuration from environment variables. No secrets in code or logs.

Required env vars:
    ANTHROPIC_API_KEY       — Claude API key for diagnostic sessions
    SYSTEM_X_ADMIN_EMAIL    — recipient for Gmail notifications
    GMAIL_SENDER_EMAIL      — Gmail account to send from
    GMAIL_APP_PASSWORD      — Gmail app password

Optional:
    SYSTEM_X_ADMIN_WHATSAPP — E.164 number for WhatsApp notifications
    TWILIO_ACCOUNT_SID      — Twilio account SID
    TWILIO_AUTH_TOKEN       — Twilio auth token
    TWILIO_WHATSAPP_FROM    — Twilio WhatsApp sender number
    PROMETHEUS_URL          — Prometheus base URL
    GPU_HOST                — GPU node IP (default 185.216.21.242)
    SYSTEM_X_DRY_RUN        — "true" to enable dry-run mode globally
    SYSTEM_X_POLICY_LEVEL   — Override policy level: LOW|MEDIUM|HIGH|CRITICAL
"""
from __future__ import annotations

import os
from typing import Any

from .classifier import IncidentClassifier
from .claude_client import SystemXClaudeClient
from .controller import SystemXController
from .correlator import IncidentCorrelator
from .evidence_collector import EvidenceCollector
from .guardrails import RecoveryGuardrails
from .health_verifier import HealthVerifier
from .notifications.engine import NotificationEngine
from .notifications.gmail import GmailNotifier
from .notifications.whatsapp import WhatsAppNotifier
from .package_builder import IncidentPackageBuilder
from .policy import RecoveryPolicyEngine
from .recovery.engine import RecoveryEngine
from .repositories.audit import SystemXAuditRepository
from .repositories.incident import SystemXIncidentRepository
from .repositories.notification import SystemXNotificationRepository
from .validator import ClaudeResponseValidator


class SystemXService:
    """Facade that assembles and exposes the System X subsystem."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._incident_repo = SystemXIncidentRepository(conn)
        self._audit_repo = SystemXAuditRepository(conn)
        self._notification_repo = SystemXNotificationRepository(conn)
        self._controller: SystemXController | None = None

    def build_controller(self) -> SystemXController:
        if self._controller is not None:
            return self._controller

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        admin_email = os.environ.get("SYSTEM_X_ADMIN_EMAIL", "prateekdas7777@gmail.com")
        gmail_sender = os.environ.get("GMAIL_SENDER_EMAIL", "")
        gmail_password = os.environ.get("GMAIL_APP_PASSWORD", "")
        admin_whatsapp = os.environ.get("SYSTEM_X_ADMIN_WHATSAPP")
        twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        twilio_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        twilio_from = os.environ.get("TWILIO_WHATSAPP_FROM", "")
        prometheus_url = os.environ.get("PROMETHEUS_URL", "http://prometheus.voiceos-ops.svc.cluster.local:9090")
        gpu_host = os.environ.get("GPU_HOST", "185.216.21.242")
        dry_run = os.environ.get("SYSTEM_X_DRY_RUN", "").lower() == "true"

        # Notification senders (gracefully absent if credentials not configured)
        gmail = GmailNotifier(gmail_sender, gmail_password) if gmail_sender and gmail_password else None
        whatsapp = (
            WhatsAppNotifier(twilio_sid, twilio_token, twilio_from)
            if twilio_sid and twilio_token and twilio_from
            else None
        )

        classifier = IncidentClassifier()
        correlator = IncidentCorrelator(classifier)
        package_builder = IncidentPackageBuilder(prometheus_url)
        evidence_collector = EvidenceCollector(prometheus_url, self._incident_repo)
        validator = ClaudeResponseValidator()
        claude_client = SystemXClaudeClient(anthropic_key, evidence_collector, validator)
        recovery_engine = RecoveryEngine(self._incident_repo, self._audit_repo)
        health_verifier = HealthVerifier(gpu_host, prometheus_url)
        notification_engine = NotificationEngine(
            self._notification_repo, gmail, whatsapp, admin_email, admin_whatsapp
        )
        policy_engine = RecoveryPolicyEngine(dry_run=dry_run)
        guardrails = RecoveryGuardrails()

        self._controller = SystemXController(
            incident_repo=self._incident_repo,
            audit_repo=self._audit_repo,
            classifier=classifier,
            correlator=correlator,
            package_builder=package_builder,
            claude_client=claude_client,
            recovery_engine=recovery_engine,
            health_verifier=health_verifier,
            notification_engine=notification_engine,
            policy_engine=policy_engine,
            guardrails=guardrails,
        )
        return self._controller

    def list_incidents(self, active_only: bool = False) -> list[Any]:
        if active_only:
            return self._incident_repo.list_active()
        return self._incident_repo.list_recent(limit=100)

    def get_incident(self, incident_id: str) -> Any | None:
        return self._incident_repo.get(incident_id)

    def get_audit_trail(self, incident_id: str) -> list[Any]:
        return self._audit_repo.list_for_incident(incident_id)

    def get_notifications(self, incident_id: str) -> list[Any]:
        return self._notification_repo.list_for_incident(incident_id)

    def get_recovery_actions(self, incident_id: str) -> list[Any]:
        return self._incident_repo.list_recovery_actions(incident_id)


__all__ = ["SystemXService"]
