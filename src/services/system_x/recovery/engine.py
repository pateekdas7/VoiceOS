"""RecoveryEngine — orchestrates policy-approved recovery actions.

Only actions approved by the RecoveryPolicyEngine may execute here.
Supports dry-run mode (simulates the plan without executing), rollback
(marks executed actions as rolled-back and records the rollback in the
audit trail), and service isolation (targets the smallest scope first).

Service isolation order (least to most destructive):
    1. CLEAR_CACHE on individual service
    2. RESTART_SERVICE on individual service
    3. SCALE_UP individual service
    4. FAILOVER to standby
    5. NOTIFY_ONCALL / MANUAL_INTERVENTION
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from ..models import (
    AuditEntry,
    ClaudeAnalysis,
    PolicyDecision,
    RecoveryAction,
    RecoveryActionStatus,
    RecoveryActionType,
)
from ..repositories.audit import SystemXAuditRepository
from ..repositories.incident import SystemXIncidentRepository
from .actions import execute_action

_log = logging.getLogger("system_x.recovery.engine")

# Ordered from least to most destructive — engine sorts the approved plan by this
_ISOLATION_ORDER: list[RecoveryActionType] = [
    RecoveryActionType.CLEAR_CACHE,
    RecoveryActionType.RESTART_SERVICE,
    RecoveryActionType.SCALE_UP,
    RecoveryActionType.FAILOVER,
    RecoveryActionType.NOTIFY_ONCALL,
    RecoveryActionType.MANUAL_INTERVENTION,
]

_STEP_ACTION_MAP: list[tuple[str, RecoveryActionType]] = [
    ("restart", RecoveryActionType.RESTART_SERVICE),
    ("scale", RecoveryActionType.SCALE_UP),
    ("cache", RecoveryActionType.CLEAR_CACHE),
    ("failover", RecoveryActionType.FAILOVER),
    ("manual", RecoveryActionType.MANUAL_INTERVENTION),
    ("escalate", RecoveryActionType.MANUAL_INTERVENTION),
    ("notify", RecoveryActionType.NOTIFY_ONCALL),
]


def _infer_action_type(step: str) -> RecoveryActionType:
    lower = step.lower()
    for keyword, action_type in _STEP_ACTION_MAP:
        if keyword in lower:
            return action_type
    return RecoveryActionType.MANUAL_INTERVENTION


def _infer_target_service(step: str, affected_services: list[str]) -> str:
    lower = step.lower()
    for svc in affected_services:
        if svc.lower() in lower or svc.replace("_", " ").lower() in lower:
            return svc
    return affected_services[0] if affected_services else "platform"


def _sort_by_isolation(
    steps: list[tuple[RecoveryActionType, str]],
) -> list[tuple[RecoveryActionType, str]]:
    """Sort (action_type, target_service) pairs by isolation order (least → most destructive)."""
    order = {a: i for i, a in enumerate(_ISOLATION_ORDER)}
    return sorted(steps, key=lambda x: order.get(x[0], 99))


class RecoveryEngine:
    def __init__(
        self,
        incident_repo: SystemXIncidentRepository,
        audit_repo: SystemXAuditRepository,
    ) -> None:
        self._incident_repo = incident_repo
        self._audit_repo = audit_repo

    async def execute_recovery_plan(
        self,
        incident_id: str,
        analysis: ClaudeAnalysis,
        affected_services: list[str],
        policy: PolicyDecision,
    ) -> list[RecoveryAction]:
        """Execute approved steps in isolation order.

        - Dry run: records DRY_RUN status, executes nothing
        - Policy filtering: skips any action not in policy.approved_actions
        - Isolation order: sorts actions from safest to most destructive
        - Stops on first FAILED action to avoid cascading harm
        Returns the list of attempted (or simulated) actions.
        """
        # Build (action_type, target_service) pairs from Claude's plan
        raw_steps = [
            (_infer_action_type(step), _infer_target_service(step, affected_services))
            for step in analysis.recovery_plan
        ]
        # Sort by isolation order
        ordered_steps = _sort_by_isolation(raw_steps)

        completed: list[RecoveryAction] = []

        for action_type, target_service in ordered_steps:
            # Policy gate: skip actions not approved
            if action_type not in policy.approved_actions:
                _log.info(
                    "incident=%s skipping %s (not in approved actions per policy %s)",
                    incident_id, action_type, policy.policy_level,
                )
                self._audit_repo.append(AuditEntry(
                    entry_id=str(uuid.uuid4()),
                    incident_id=incident_id,
                    recorded_at=datetime.now(UTC),
                    actor="system_x.policy",
                    action=f"skip_action:{action_type}:{target_service}",
                    result=f"blocked by policy {policy.policy_level}",
                ))
                continue

            action_id = str(uuid.uuid4())
            now = datetime.now(UTC)

            if policy.dry_run:
                # Dry run — simulate the action
                action = RecoveryAction(
                    action_id=action_id,
                    incident_id=incident_id,
                    action_type=action_type,
                    target_service=target_service,
                    status=RecoveryActionStatus.DRY_RUN,
                    started_at=now,
                    completed_at=now,
                    result=f"[DRY RUN] would execute {action_type} on {target_service}",
                )
                self._incident_repo.create_recovery_action(action)
                self._audit_repo.append(AuditEntry(
                    entry_id=str(uuid.uuid4()),
                    incident_id=incident_id,
                    recorded_at=now,
                    actor="system_x.dry_run",
                    action=f"simulated_action:{action_type}:{target_service}",
                    result="DRY_RUN",
                ))
                completed.append(action)
                _log.info("incident=%s DRY_RUN %s on %s", incident_id, action_type, target_service)
                continue

            # Live execution
            action = RecoveryAction(
                action_id=action_id,
                incident_id=incident_id,
                action_type=action_type,
                target_service=target_service,
                status=RecoveryActionStatus.IN_PROGRESS,
                started_at=now,
            )
            self._incident_repo.create_recovery_action(action)
            self._audit_repo.append(AuditEntry(
                entry_id=str(uuid.uuid4()),
                incident_id=incident_id,
                recorded_at=now,
                actor="system_x",
                action=f"start_action:{action_type}:{target_service}",
                metadata={"policy_level": str(policy.policy_level)},
            ))

            success, result_msg = await execute_action(str(action_type), target_service)
            finished_at = datetime.now(UTC)
            final_status = RecoveryActionStatus.COMPLETED if success else RecoveryActionStatus.FAILED

            self._incident_repo.update_recovery_action(
                action_id,
                final_status,
                completed_at=finished_at,
                result=result_msg if success else None,
                error=result_msg if not success else None,
            )
            self._audit_repo.append(AuditEntry(
                entry_id=str(uuid.uuid4()),
                incident_id=incident_id,
                recorded_at=finished_at,
                actor="system_x",
                action=f"complete_action:{action_type}:{target_service}",
                result="success" if success else "failed",
            ))

            completed_action = RecoveryAction(
                action_id=action_id,
                incident_id=incident_id,
                action_type=action_type,
                target_service=target_service,
                status=final_status,
                started_at=now,
                completed_at=finished_at,
                result=result_msg if success else None,
                error=result_msg if not success else None,
            )
            completed.append(completed_action)
            _log.info("incident=%s action=%s service=%s status=%s", incident_id, action_type, target_service, final_status)

            if not success:
                _log.warning("incident=%s recovery halted — action %s failed", incident_id, action_type)
                break

        return completed

    async def rollback(
        self,
        incident_id: str,
        completed_actions: list[RecoveryAction],
        reason: str,
    ) -> list[RecoveryAction]:
        """Mark completed actions as rolled back and record in audit trail.

        For the current single-GPU topology, rollback is primarily a record-keeping
        operation (most actions are non-reversible start signals). The audit record
        is what matters — operators see exactly what happened and when.
        """
        rolled_back: list[RecoveryAction] = []
        now = datetime.now(UTC)

        self._audit_repo.append(AuditEntry(
            entry_id=str(uuid.uuid4()),
            incident_id=incident_id,
            recorded_at=now,
            actor="system_x",
            action="rollback_initiated",
            rollback_status="in_progress",
            result=reason,
        ))

        for action in reversed(completed_actions):
            if action.status != RecoveryActionStatus.COMPLETED:
                continue

            self._incident_repo.update_recovery_action(
                action.action_id,
                RecoveryActionStatus.ROLLED_BACK,
                rolled_back=True,
                error=f"Rolled back: {reason}",
            )
            self._audit_repo.append(AuditEntry(
                entry_id=str(uuid.uuid4()),
                incident_id=incident_id,
                recorded_at=now,
                actor="system_x",
                action=f"rollback_action:{action.action_type}:{action.target_service}",
                rollback_status="completed",
                result=reason,
            ))
            rolled_back.append(RecoveryAction(
                action_id=action.action_id,
                incident_id=incident_id,
                action_type=action.action_type,
                target_service=action.target_service,
                status=RecoveryActionStatus.ROLLED_BACK,
                started_at=action.started_at,
                completed_at=action.completed_at,
                rolled_back=True,
                error=f"Rolled back: {reason}",
            ))
            _log.info("incident=%s rolled back %s on %s", incident_id, action.action_type, action.target_service)

        self._audit_repo.append(AuditEntry(
            entry_id=str(uuid.uuid4()),
            incident_id=incident_id,
            recorded_at=datetime.now(UTC),
            actor="system_x",
            action="rollback_complete",
            rollback_status="completed",
            result=f"rolled back {len(rolled_back)} actions",
        ))

        return rolled_back

    def build_recovery_summary(self, actions: list[RecoveryAction]) -> str:
        if not actions:
            return "No recovery actions were executed."
        parts = []
        for a in actions:
            if a.status == RecoveryActionStatus.DRY_RUN:
                state = "DRY_RUN"
            elif a.status == RecoveryActionStatus.COMPLETED:
                state = "OK"
            elif a.status == RecoveryActionStatus.ROLLED_BACK:
                state = "ROLLED_BACK"
            else:
                state = "FAILED"
            svc = a.target_service or "platform"
            parts.append(f"[{state}] {a.action_type} on {svc}")
        return "; ".join(parts)


__all__ = ["RecoveryEngine", "_infer_action_type"]
