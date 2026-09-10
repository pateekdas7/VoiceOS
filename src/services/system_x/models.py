"""System X domain models — incidents, recovery, audit, notifications, governance."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


# ---------------------------------------------------------------------------
# Core incident enums
# ---------------------------------------------------------------------------

class IncidentSeverity(StrEnum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class IncidentStatus(StrEnum):
    DETECTING = "DETECTING"
    ANALYZING = "ANALYZING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    RECOVERING = "RECOVERING"
    VERIFYING = "VERIFYING"
    ROLLING_BACK = "ROLLING_BACK"
    RESOLVED = "RESOLVED"
    FAILED = "FAILED"


class RecoveryActionType(StrEnum):
    RESTART_SERVICE = "RESTART_SERVICE"
    SCALE_UP = "SCALE_UP"
    CLEAR_CACHE = "CLEAR_CACHE"
    FAILOVER = "FAILOVER"
    NOTIFY_ONCALL = "NOTIFY_ONCALL"
    MANUAL_INTERVENTION = "MANUAL_INTERVENTION"


class RecoveryActionStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    DRY_RUN = "DRY_RUN"


class NotificationChannel(StrEnum):
    GMAIL = "gmail"
    WHATSAPP = "whatsapp"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Governance / policy enums
# ---------------------------------------------------------------------------

class PolicyLevel(StrEnum):
    """Recovery automation level per incident severity."""
    LOW = "LOW"          # Full auto-recovery, all non-destructive actions
    MEDIUM = "MEDIUM"    # Full auto-recovery + notification, all actions
    HIGH = "HIGH"        # Auto for safe actions, human approval for destructive
    CRITICAL = "CRITICAL"  # Human approval required before any action


class ValidationOutcome(StrEnum):
    VALID = "VALID"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    CONFIDENCE_TOO_LOW = "CONFIDENCE_TOO_LOW"
    FORBIDDEN_OPERATION = "FORBIDDEN_OPERATION"
    SECRET_LEAK = "SECRET_LEAK"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    TIMEOUT = "TIMEOUT"
    MALFORMED = "MALFORMED"


# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClaudeAnalysis:
    root_cause: str
    confidence: str  # "low" | "medium" | "high"
    recommended_actions: tuple[str, ...]
    recovery_plan: tuple[str, ...]  # ordered steps
    estimated_recovery_time_s: int
    risk_assessment: str
    model: str
    analyzed_at: datetime
    # Agentic session metadata (optional — populated by DiagnosticSession)
    conversation_id: str | None = None
    turn_count: int = 1
    evidence_keys: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PolicyDecision:
    """Result of the Recovery Policy Engine evaluation."""
    allowed: bool
    policy_level: PolicyLevel
    approved_actions: frozenset[RecoveryActionType]
    requires_human_approval: bool
    dry_run: bool
    reason: str


@dataclass(frozen=True)
class ValidationResult:
    """Result of ClaudeResponseValidator.validate()."""
    outcome: ValidationOutcome
    valid: bool
    message: str
    analysis: ClaudeAnalysis | None = None  # populated only when valid=True


@dataclass(frozen=True)
class DiagnosticTurn:
    """One turn of the bidirectional diagnostic session with Claude."""
    turn: int
    role: str   # "user" | "assistant"
    tools_called: tuple[str, ...]
    evidence_fetched: tuple[str, ...]
    content_summary: str  # redacted summary, not raw payload


@dataclass(frozen=True)
class IncidentRecord:
    incident_id: str
    title: str
    severity: IncidentSeverity
    status: IncidentStatus
    detected_at: datetime
    affected_services: tuple[str, ...]
    alert_fingerprints: tuple[str, ...]
    resolved_at: datetime | None = None
    root_cause: str | None = None
    recovery_summary: str | None = None
    claude_analysis: ClaudeAnalysis | None = None
    health_after: dict[str, Any] = field(default_factory=dict)
    total_downtime_s: int | None = None
    notifications_sent: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecoveryAction:
    action_id: str
    incident_id: str
    action_type: RecoveryActionType
    status: RecoveryActionStatus
    started_at: datetime
    target_service: str | None = None
    completed_at: datetime | None = None
    result: str | None = None
    error: str | None = None
    rolled_back: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuditEntry:
    entry_id: str
    recorded_at: datetime
    actor: str
    action: str
    incident_id: str | None = None
    result: str | None = None
    rollback_status: str | None = None
    verification_outcome: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NotificationRecord:
    notification_id: str
    incident_id: str
    channel: NotificationChannel
    notification_type: str  # "incident_start" | "recovery_progress" | "resolved" | "approval_required"
    recipient: str
    body: str
    status: NotificationStatus
    created_at: datetime
    subject: str | None = None
    sent_at: datetime | None = None
    error: str | None = None


@dataclass(frozen=True)
class IngestAlert:
    """A normalized inbound alert from Alertmanager or compliance monitoring."""
    fingerprint: str
    alert_name: str
    severity: str
    service: str
    labels: dict[str, str]
    annotations: dict[str, str]
    fired_at: datetime


__all__ = [
    "AuditEntry",
    "ClaudeAnalysis",
    "DiagnosticTurn",
    "IncidentRecord",
    "IncidentSeverity",
    "IncidentStatus",
    "IngestAlert",
    "NotificationChannel",
    "NotificationRecord",
    "NotificationStatus",
    "PolicyDecision",
    "PolicyLevel",
    "RecoveryAction",
    "RecoveryActionStatus",
    "RecoveryActionType",
    "ValidationOutcome",
    "ValidationResult",
]
