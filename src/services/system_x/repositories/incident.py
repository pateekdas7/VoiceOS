"""SystemXIncidentRepository — persists incidents and recovery actions."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ..models import (
    ClaudeAnalysis,
    IncidentRecord,
    IncidentSeverity,
    IncidentStatus,
    RecoveryAction,
    RecoveryActionStatus,
    RecoveryActionType,
)


def _dt(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=UTC) if val.tzinfo is None else val
    return datetime.fromisoformat(str(val)).replace(tzinfo=UTC)


def _analysis_to_json(a: ClaudeAnalysis) -> str:
    return json.dumps({
        "root_cause": a.root_cause,
        "confidence": a.confidence,
        "recommended_actions": list(a.recommended_actions),
        "recovery_plan": list(a.recovery_plan),
        "estimated_recovery_time_s": a.estimated_recovery_time_s,
        "risk_assessment": a.risk_assessment,
        "model": a.model,
        "analyzed_at": a.analyzed_at.isoformat(),
        "conversation_id": a.conversation_id,
        "turn_count": a.turn_count,
        "evidence_keys": list(a.evidence_keys),
    })


def _analysis_from_dict(d: dict) -> ClaudeAnalysis:
    return ClaudeAnalysis(
        root_cause=d["root_cause"],
        confidence=d["confidence"],
        recommended_actions=tuple(d.get("recommended_actions", [])),
        recovery_plan=tuple(d.get("recovery_plan", [])),
        estimated_recovery_time_s=int(d.get("estimated_recovery_time_s", 300)),
        risk_assessment=d.get("risk_assessment", ""),
        model=d.get("model", ""),
        analyzed_at=datetime.fromisoformat(d["analyzed_at"]).replace(tzinfo=UTC),
        conversation_id=d.get("conversation_id"),
        turn_count=int(d.get("turn_count", 1)),
        evidence_keys=tuple(d.get("evidence_keys", [])),
    )


def _row_to_incident(row: tuple) -> IncidentRecord:
    (
        incident_id, title, severity, status, detected_at, resolved_at,
        affected_services, alert_fingerprints, root_cause, recovery_summary,
        claude_analysis, health_after, total_downtime_s, notifications_sent, metadata,
    ) = row
    analysis = _analysis_from_dict(claude_analysis) if claude_analysis else None
    return IncidentRecord(
        incident_id=incident_id,
        title=title,
        severity=IncidentSeverity(severity),
        status=IncidentStatus(status),
        detected_at=_dt(detected_at),
        resolved_at=_dt(resolved_at),
        affected_services=tuple(affected_services or []),
        alert_fingerprints=tuple(alert_fingerprints or []),
        root_cause=root_cause,
        recovery_summary=recovery_summary,
        claude_analysis=analysis,
        health_after=health_after or {},
        total_downtime_s=total_downtime_s,
        notifications_sent=tuple(notifications_sent or []),
        metadata=metadata or {},
    )


def _row_to_action(row: tuple) -> RecoveryAction:
    (
        action_id, incident_id, action_type, target_service, status,
        started_at, completed_at, result, error, rolled_back, metadata,
    ) = row
    return RecoveryAction(
        action_id=action_id,
        incident_id=incident_id,
        action_type=RecoveryActionType(action_type),
        target_service=target_service,
        status=RecoveryActionStatus(status),
        started_at=_dt(started_at),
        completed_at=_dt(completed_at),
        result=result,
        error=error,
        rolled_back=bool(rolled_back),
        metadata=metadata or {},
    )


class SystemXIncidentRepository:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def create(self, incident: IncidentRecord) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO system_x_incidents
                    (incident_id, title, severity, status, detected_at, resolved_at,
                     affected_services, alert_fingerprints, root_cause, recovery_summary,
                     claude_analysis, health_after, total_downtime_s, notifications_sent, metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (incident_id) DO NOTHING
                """,
                (
                    incident.incident_id, incident.title, str(incident.severity),
                    str(incident.status), incident.detected_at, incident.resolved_at,
                    list(incident.affected_services), list(incident.alert_fingerprints),
                    incident.root_cause, incident.recovery_summary,
                    _analysis_to_json(incident.claude_analysis) if incident.claude_analysis else None,
                    json.dumps(incident.health_after),
                    incident.total_downtime_s, list(incident.notifications_sent),
                    json.dumps(incident.metadata),
                ),
            )
        self._conn.commit()

    def update_status(
        self,
        incident_id: str,
        status: IncidentStatus,
        *,
        resolved_at: datetime | None = None,
        root_cause: str | None = None,
        recovery_summary: str | None = None,
        claude_analysis: ClaudeAnalysis | None = None,
        health_after: dict | None = None,
        total_downtime_s: int | None = None,
        notifications_sent: list[str] | None = None,
    ) -> None:
        sets = ["status = %s"]
        vals: list[Any] = [str(status)]
        if resolved_at is not None:
            sets.append("resolved_at = %s"); vals.append(resolved_at)
        if root_cause is not None:
            sets.append("root_cause = %s"); vals.append(root_cause)
        if recovery_summary is not None:
            sets.append("recovery_summary = %s"); vals.append(recovery_summary)
        if claude_analysis is not None:
            sets.append("claude_analysis = %s"); vals.append(_analysis_to_json(claude_analysis))
        if health_after is not None:
            sets.append("health_after = %s"); vals.append(json.dumps(health_after))
        if total_downtime_s is not None:
            sets.append("total_downtime_s = %s"); vals.append(total_downtime_s)
        if notifications_sent is not None:
            sets.append("notifications_sent = %s"); vals.append(notifications_sent)
        vals.append(incident_id)
        with self._conn.cursor() as cur:
            cur.execute(f"UPDATE system_x_incidents SET {', '.join(sets)} WHERE incident_id = %s", vals)
        self._conn.commit()

    def get(self, incident_id: str) -> IncidentRecord | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT incident_id, title, severity, status, detected_at, resolved_at,
                          affected_services, alert_fingerprints, root_cause, recovery_summary,
                          claude_analysis, health_after, total_downtime_s, notifications_sent, metadata
                   FROM system_x_incidents WHERE incident_id = %s""",
                (incident_id,),
            )
            row = cur.fetchone()
        return _row_to_incident(row) if row else None

    def list_active(self) -> list[IncidentRecord]:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT incident_id, title, severity, status, detected_at, resolved_at,
                          affected_services, alert_fingerprints, root_cause, recovery_summary,
                          claude_analysis, health_after, total_downtime_s, notifications_sent, metadata
                   FROM system_x_incidents
                   WHERE status NOT IN ('RESOLVED','FAILED')
                   ORDER BY detected_at DESC"""
            )
            rows = cur.fetchall()
        return [_row_to_incident(r) for r in rows]

    def list_recent(self, limit: int = 50) -> list[IncidentRecord]:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT incident_id, title, severity, status, detected_at, resolved_at,
                          affected_services, alert_fingerprints, root_cause, recovery_summary,
                          claude_analysis, health_after, total_downtime_s, notifications_sent, metadata
                   FROM system_x_incidents ORDER BY detected_at DESC LIMIT %s""",
                (limit,),
            )
            rows = cur.fetchall()
        return [_row_to_incident(r) for r in rows]

    def create_recovery_action(self, action: RecoveryAction) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO system_x_recovery_actions
                       (action_id, incident_id, action_type, target_service, status,
                        started_at, completed_at, result, error, rolled_back, metadata)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (action_id) DO NOTHING""",
                (
                    action.action_id, action.incident_id, str(action.action_type),
                    action.target_service, str(action.status), action.started_at,
                    action.completed_at, action.result, action.error,
                    action.rolled_back, json.dumps(action.metadata),
                ),
            )
        self._conn.commit()

    def update_recovery_action(
        self,
        action_id: str,
        status: RecoveryActionStatus,
        *,
        completed_at: datetime | None = None,
        result: str | None = None,
        error: str | None = None,
        rolled_back: bool | None = None,
    ) -> None:
        sets = ["status = %s"]
        vals: list[Any] = [str(status)]
        if completed_at is not None:
            sets.append("completed_at = %s"); vals.append(completed_at)
        if result is not None:
            sets.append("result = %s"); vals.append(result)
        if error is not None:
            sets.append("error = %s"); vals.append(error)
        if rolled_back is not None:
            sets.append("rolled_back = %s"); vals.append(rolled_back)
        vals.append(action_id)
        with self._conn.cursor() as cur:
            cur.execute(f"UPDATE system_x_recovery_actions SET {', '.join(sets)} WHERE action_id = %s", vals)
        self._conn.commit()

    def list_recovery_actions(self, incident_id: str) -> list[RecoveryAction]:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT action_id, incident_id, action_type, target_service, status,
                          started_at, completed_at, result, error, rolled_back, metadata
                   FROM system_x_recovery_actions WHERE incident_id = %s ORDER BY started_at""",
                (incident_id,),
            )
            rows = cur.fetchall()
        return [_row_to_action(r) for r in rows]


__all__ = ["SystemXIncidentRepository"]
