"""Notification message templates for System X incidents."""
from __future__ import annotations

from datetime import datetime

from ..models import ClaudeAnalysis, IncidentRecord, IncidentSeverity


def _severity_emoji(severity: IncidentSeverity) -> str:
    return "🔴" if severity == IncidentSeverity.CRITICAL else "🟡"


def incident_start_subject(incident: IncidentRecord) -> str:
    icon = _severity_emoji(incident.severity)
    return f"{icon} [{incident.severity}] VoiceOS Incident Detected: {incident.title}"


def incident_start_body(incident: IncidentRecord) -> str:
    services = ", ".join(incident.affected_services) or "unknown"
    alerts = ", ".join(incident.alert_fingerprints[:5]) or "none"
    return (
        f"System X has detected a {incident.severity} incident.\n\n"
        f"Incident ID: {incident.incident_id}\n"
        f"Title: {incident.title}\n"
        f"Severity: {incident.severity}\n"
        f"Detected at: {incident.detected_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"Affected services: {services}\n"
        f"Alert fingerprints: {alerts}\n\n"
        f"System X is now analyzing the incident. You will receive updates automatically."
    )


def incident_start_whatsapp(incident: IncidentRecord) -> str:
    icon = _severity_emoji(incident.severity)
    services = ", ".join(incident.affected_services) or "unknown"
    return (
        f"{icon} *VoiceOS {incident.severity} Incident*\n"
        f"ID: `{incident.incident_id[:8]}`\n"
        f"*{incident.title}*\n"
        f"Services: {services}\n"
        f"Detected: {incident.detected_at.strftime('%H:%M UTC')}\n"
        f"System X is analyzing..."
    )


def analysis_complete_subject(incident: IncidentRecord) -> str:
    return f"[System X] Analysis complete for incident {incident.incident_id[:8]}: {incident.title}"


def analysis_complete_body(incident: IncidentRecord, analysis: ClaudeAnalysis) -> str:
    steps = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(analysis.recovery_plan))
    actions = "\n".join(f"  - {a}" for a in analysis.recommended_actions)
    return (
        f"Claude has completed analysis for incident {incident.incident_id}.\n\n"
        f"ROOT CAUSE ({analysis.confidence} confidence):\n{analysis.root_cause}\n\n"
        f"RECOVERY PLAN:\n{steps}\n\n"
        f"RECOMMENDED ACTIONS:\n{actions}\n\n"
        f"RISK ASSESSMENT:\n{analysis.risk_assessment}\n\n"
        f"Estimated recovery time: {analysis.estimated_recovery_time_s}s\n"
        f"System X is now executing recovery actions automatically."
    )


def analysis_complete_whatsapp(incident: IncidentRecord, analysis: ClaudeAnalysis) -> str:
    return (
        f"🔍 *Analysis complete* for `{incident.incident_id[:8]}`\n"
        f"Root cause ({analysis.confidence}): {analysis.root_cause[:200]}\n"
        f"ETA: {analysis.estimated_recovery_time_s}s\n"
        f"Executing recovery..."
    )


def resolved_subject(incident: IncidentRecord) -> str:
    return f"[System X] ✅ Incident resolved: {incident.title}"


def resolved_body(incident: IncidentRecord) -> str:
    downtime = f"{incident.total_downtime_s}s" if incident.total_downtime_s else "unknown"
    resolved_at = incident.resolved_at.strftime('%Y-%m-%d %H:%M:%S UTC') if incident.resolved_at else "N/A"
    return (
        f"Incident {incident.incident_id} has been resolved.\n\n"
        f"Title: {incident.title}\n"
        f"Resolved at: {resolved_at}\n"
        f"Total downtime: {downtime}\n\n"
        f"RECOVERY SUMMARY:\n{incident.recovery_summary or 'No summary available.'}\n\n"
        f"ROOT CAUSE:\n{incident.root_cause or 'See Claude analysis for details.'}"
    )


def resolved_whatsapp(incident: IncidentRecord) -> str:
    downtime = f"{incident.total_downtime_s}s" if incident.total_downtime_s else "?"
    return (
        f"✅ *Incident resolved*: `{incident.incident_id[:8]}`\n"
        f"{incident.title}\n"
        f"Downtime: {downtime}\n"
        f"{incident.recovery_summary or ''}"
    )


def approval_required_subject(incident: IncidentRecord) -> str:
    return f"[System X] ⚠️ Human Approval Required: {incident.title}"


def approval_required_body(incident: IncidentRecord, reason: str) -> str:
    services = ", ".join(incident.affected_services) or "unknown"
    return (
        f"System X has detected incident {incident.incident_id} but requires human approval "
        f"before executing recovery actions.\n\n"
        f"Incident: {incident.title}\n"
        f"Severity: {incident.severity}\n"
        f"Services: {services}\n\n"
        f"POLICY DECISION:\n{reason}\n\n"
        f"To approve, visit the System X dashboard in the VoiceOS admin panel "
        f"and acknowledge this incident. System X will not take any automated "
        f"action until approved."
    )


def approval_required_whatsapp(incident: IncidentRecord, reason: str) -> str:
    return (
        f"⚠️ *Human Approval Required*\n"
        f"Incident: `{incident.incident_id[:8]}`\n"
        f"*{incident.title}*\n"
        f"Reason: {reason[:200]}\n"
        f"Visit the admin panel to approve recovery."
    )


__all__ = [
    "analysis_complete_body", "analysis_complete_subject", "analysis_complete_whatsapp",
    "approval_required_body", "approval_required_subject", "approval_required_whatsapp",
    "incident_start_body", "incident_start_subject", "incident_start_whatsapp",
    "resolved_body", "resolved_subject", "resolved_whatsapp",
]
