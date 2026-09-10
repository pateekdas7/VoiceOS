"""PostgresAlertRepository -- backs AlertRepositoryPort against ``alert_history`` (migration 0029).

Owned by ``plumbing/`` at the port level, but this concrete implementation
lives outside both ``plumbing/`` and ``reasoning/`` (a persistence-adapter
layer), consistent with how every other repository in this codebase is a
thin adapter injected into its service, not embedded inside it.
"""

from __future__ import annotations

import json
from typing import Any

from src.libs.repositories.base import BaseRepository
from src.services.ops_intelligence.models import AlertRecord, AlertSource, AlertStatus, Severity

_TABLE = "alert_history"
_COLUMNS = (
    "alert_id",
    "tenant_id",
    "source",
    "fingerprint",
    "severity",
    "status",
    "fired_at",
    "acknowledged_at",
    "acknowledged_by",
    "escalated_at",
    "escalated_to",
    "resolved_at",
    "labels",
    "annotations",
)


class PostgresAlertRepository(BaseRepository):
    """Real Postgres-backed ``AlertRepositoryPort`` implementation."""

    def create(self, alert: AlertRecord) -> AlertRecord:
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                alert_id, tenant_id, source, fingerprint, severity, status, fired_at,
                acknowledged_at, acknowledged_by, escalated_at, escalated_to, resolved_at,
                labels, annotations
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
            """,
            (
                alert.alert_id,
                alert.tenant_id,
                alert.source.value,
                alert.fingerprint,
                alert.severity.value,
                alert.status.value,
                alert.fired_at,
                alert.acknowledged_at,
                alert.acknowledged_by,
                alert.escalated_at,
                alert.escalated_to,
                alert.resolved_at,
                json.dumps(alert.labels),
                json.dumps(alert.annotations),
            ),
        )
        self._commit()
        return alert

    def get(self, alert_id: str) -> AlertRecord | None:
        cur = self._execute(f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} WHERE alert_id = %s", (alert_id,))
        row = cur.fetchone()
        return _row_to_alert(row) if row is not None else None

    def find_by_fingerprint_open(self, fingerprint: str) -> AlertRecord | None:
        cur = self._execute(
            f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} WHERE fingerprint = %s AND status != %s "
            "ORDER BY fired_at DESC LIMIT 1",
            (fingerprint, AlertStatus.RESOLVED.value),
        )
        row = cur.fetchone()
        return _row_to_alert(row) if row is not None else None

    def update_status(self, alert_id: str, alert: AlertRecord) -> AlertRecord:
        self._execute(
            f"""
            UPDATE {_TABLE} SET
                status = %s, acknowledged_at = %s, acknowledged_by = %s,
                escalated_at = %s, escalated_to = %s, resolved_at = %s
            WHERE alert_id = %s
            """,
            (
                alert.status.value,
                alert.acknowledged_at,
                alert.acknowledged_by,
                alert.escalated_at,
                alert.escalated_to,
                alert.resolved_at,
                alert_id,
            ),
        )
        self._commit()
        return alert

    def list_open(self, tenant_id: str | None = None) -> tuple[AlertRecord, ...]:
        where = "status != %s"
        params: list[Any] = [AlertStatus.RESOLVED.value]
        if tenant_id is not None:
            where += " AND tenant_id = %s"
            params.append(tenant_id)
        cur = self._execute(
            f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} WHERE {where} ORDER BY fired_at ASC", params
        )
        return tuple(_row_to_alert(row) for row in cur.fetchall())

    def count_by_status(self) -> dict[tuple[AlertStatus, str], int]:
        cur = self._execute(
            f"SELECT status, source, COUNT(*) FROM {_TABLE} WHERE status != %s GROUP BY status, source",
            (AlertStatus.RESOLVED.value,),
        )
        return {(AlertStatus(status), source): count for status, source, count in cur.fetchall()}


def _row_to_alert(row: tuple[Any, ...]) -> AlertRecord:
    (
        alert_id,
        tenant_id,
        source,
        fingerprint,
        severity,
        status,
        fired_at,
        acknowledged_at,
        acknowledged_by,
        escalated_at,
        escalated_to,
        resolved_at,
        labels,
        annotations,
    ) = row
    return AlertRecord(
        alert_id=str(alert_id),
        tenant_id=str(tenant_id) if tenant_id is not None else None,
        source=AlertSource(source),
        fingerprint=fingerprint,
        severity=Severity(severity),
        status=AlertStatus(status),
        fired_at=fired_at,
        labels=json.loads(labels) if isinstance(labels, str) else labels,
        annotations=json.loads(annotations) if isinstance(annotations, str) else annotations,
        acknowledged_at=acknowledged_at,
        acknowledged_by=acknowledged_by,
        escalated_at=escalated_at,
        escalated_to=escalated_to,
        resolved_at=resolved_at,
    )


__all__ = ["PostgresAlertRepository"]
