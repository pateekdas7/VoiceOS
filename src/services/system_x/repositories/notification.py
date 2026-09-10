"""SystemXNotificationRepository — notification record persistence."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..models import NotificationChannel, NotificationRecord, NotificationStatus


def _dt(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=UTC) if val.tzinfo is None else val
    return datetime.fromisoformat(str(val)).replace(tzinfo=UTC)


def _row_to_record(row: tuple) -> NotificationRecord:
    (
        notification_id, incident_id, channel, notification_type, recipient,
        subject, body, status, sent_at, error, created_at,
    ) = row
    return NotificationRecord(
        notification_id=notification_id,
        incident_id=incident_id,
        channel=NotificationChannel(channel),
        notification_type=notification_type,
        recipient=recipient,
        subject=subject,
        body=body,
        status=NotificationStatus(status),
        sent_at=_dt(sent_at),
        error=error,
        created_at=_dt(created_at),
    )


class SystemXNotificationRepository:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def create(self, record: NotificationRecord) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO system_x_notifications
                       (notification_id, incident_id, channel, notification_type, recipient,
                        subject, body, status, sent_at, error, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (notification_id) DO NOTHING""",
                (
                    record.notification_id, record.incident_id, str(record.channel),
                    record.notification_type, record.recipient, record.subject,
                    record.body, str(record.status), record.sent_at, record.error,
                    record.created_at,
                ),
            )
        self._conn.commit()

    def update_sent(self, notification_id: str, sent_at: datetime, error: str | None = None) -> None:
        status = "failed" if error else "sent"
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE system_x_notifications SET status=%s, sent_at=%s, error=%s WHERE notification_id=%s",
                (status, sent_at, error, notification_id),
            )
        self._conn.commit()

    def list_for_incident(self, incident_id: str) -> list[NotificationRecord]:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT notification_id, incident_id, channel, notification_type, recipient,
                          subject, body, status, sent_at, error, created_at
                   FROM system_x_notifications WHERE incident_id = %s ORDER BY created_at""",
                (incident_id,),
            )
            rows = cur.fetchall()
        return [_row_to_record(r) for r in rows]


__all__ = ["SystemXNotificationRepository"]
