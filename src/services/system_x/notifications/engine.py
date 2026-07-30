"""NotificationEngine — orchestrates Gmail + WhatsApp notifications for System X."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from ..models import (
    ClaudeAnalysis,
    IncidentRecord,
    NotificationChannel,
    NotificationRecord,
    NotificationStatus,
)
from ..repositories.notification import SystemXNotificationRepository
from . import templates
from .gmail import GmailNotifier
from .whatsapp import WhatsAppNotifier

# Import policy type (lazy to avoid circular)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    pass

_log = logging.getLogger("system_x.notifications.engine")


class NotificationEngine:
    def __init__(
        self,
        notification_repo: SystemXNotificationRepository,
        gmail: GmailNotifier | None,
        whatsapp: WhatsAppNotifier | None,
        admin_email: str,
        admin_whatsapp: str | None = None,
    ) -> None:
        self._repo = notification_repo
        self._gmail = gmail
        self._whatsapp = whatsapp
        self._admin_email = admin_email
        self._admin_whatsapp = admin_whatsapp

    async def notify_incident_start(self, incident: IncidentRecord) -> list[str]:
        """Send incident-detected notifications. Returns list of notification_ids sent."""
        sent_ids: list[str] = []
        subject = templates.incident_start_subject(incident)
        body = templates.incident_start_body(incident)
        whatsapp_body = templates.incident_start_whatsapp(incident)

        if self._gmail:
            nid = await self._send_gmail(incident.incident_id, "incident_start", self._admin_email, subject, body)
            if nid:
                sent_ids.append(nid)

        if self._whatsapp and self._admin_whatsapp:
            nid = await self._send_whatsapp(incident.incident_id, "incident_start", self._admin_whatsapp, whatsapp_body)
            if nid:
                sent_ids.append(nid)

        return sent_ids

    async def notify_analysis_complete(self, incident: IncidentRecord, analysis: ClaudeAnalysis) -> list[str]:
        sent_ids: list[str] = []
        subject = templates.analysis_complete_subject(incident)
        body = templates.analysis_complete_body(incident, analysis)
        whatsapp_body = templates.analysis_complete_whatsapp(incident, analysis)

        if self._gmail:
            nid = await self._send_gmail(incident.incident_id, "recovery_progress", self._admin_email, subject, body)
            if nid:
                sent_ids.append(nid)

        if self._whatsapp and self._admin_whatsapp:
            nid = await self._send_whatsapp(incident.incident_id, "recovery_progress", self._admin_whatsapp, whatsapp_body)
            if nid:
                sent_ids.append(nid)

        return sent_ids

    async def notify_resolved(self, incident: IncidentRecord) -> list[str]:
        sent_ids: list[str] = []
        subject = templates.resolved_subject(incident)
        body = templates.resolved_body(incident)
        whatsapp_body = templates.resolved_whatsapp(incident)

        if self._gmail:
            nid = await self._send_gmail(incident.incident_id, "resolved", self._admin_email, subject, body)
            if nid:
                sent_ids.append(nid)

        if self._whatsapp and self._admin_whatsapp:
            nid = await self._send_whatsapp(incident.incident_id, "resolved", self._admin_whatsapp, whatsapp_body)
            if nid:
                sent_ids.append(nid)

        return sent_ids

    async def _send_gmail(
        self,
        incident_id: str,
        notification_type: str,
        recipient: str,
        subject: str,
        body: str,
    ) -> str | None:
        nid = str(uuid.uuid4())
        now = datetime.now(UTC)
        record = NotificationRecord(
            notification_id=nid,
            incident_id=incident_id,
            channel=NotificationChannel.GMAIL,
            notification_type=notification_type,
            recipient=recipient,
            subject=subject,
            body=body,
            status=NotificationStatus.PENDING,
            created_at=now,
        )
        self._repo.create(record)
        try:
            assert self._gmail is not None
            self._gmail.send(recipient, subject, body)
            self._repo.update_sent(nid, datetime.now(UTC))
            return nid
        except Exception as exc:
            _log.error("gmail notification failed incident=%s: %s", incident_id, exc)
            self._repo.update_sent(nid, datetime.now(UTC), error=str(exc))
            return None

    async def _send_whatsapp(
        self,
        incident_id: str,
        notification_type: str,
        to_number: str,
        body: str,
    ) -> str | None:
        nid = str(uuid.uuid4())
        now = datetime.now(UTC)
        record = NotificationRecord(
            notification_id=nid,
            incident_id=incident_id,
            channel=NotificationChannel.WHATSAPP,
            notification_type=notification_type,
            recipient=to_number,
            body=body,
            status=NotificationStatus.PENDING,
            created_at=now,
        )
        self._repo.create(record)
        try:
            assert self._whatsapp is not None
            await self._whatsapp.send(to_number, body)
            self._repo.update_sent(nid, datetime.now(UTC))
            return nid
        except Exception as exc:
            _log.error("whatsapp notification failed incident=%s: %s", incident_id, exc)
            self._repo.update_sent(nid, datetime.now(UTC), error=str(exc))
            return None


    async def notify_approval_required(self, incident: IncidentRecord, reason: str) -> list[str]:
        """Send human-approval-required notifications. Returns list of notification_ids sent."""
        sent_ids: list[str] = []
        subject = templates.approval_required_subject(incident)
        body = templates.approval_required_body(incident, reason)
        whatsapp_body = templates.approval_required_whatsapp(incident, reason)

        if self._gmail:
            nid = await self._send_gmail(incident.incident_id, "approval_required", self._admin_email, subject, body)
            if nid:
                sent_ids.append(nid)

        if self._whatsapp and self._admin_whatsapp:
            nid = await self._send_whatsapp(incident.incident_id, "approval_required", self._admin_whatsapp, whatsapp_body)
            if nid:
                sent_ids.append(nid)

        return sent_ids


__all__ = ["NotificationEngine"]
