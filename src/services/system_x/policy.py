"""RecoveryPolicyEngine — governs all automated recovery decisions.

Claude's analysis is advisory. The Policy Engine makes the final decision
about what System X is allowed to execute. Claude can never directly control
production systems — it can only recommend. The Policy Engine approves or
rejects those recommendations based on severity, action type, and context.

Decision matrix:
    WARNING  → LOW    : Full auto-recovery, all non-destructive actions allowed
    CRITICAL → HIGH   : Auto for safe actions; human approval for destructive
    (configurable per deployment via SYSTEM_X_POLICY_LEVEL env var)

Forbidden operations (never automated, regardless of policy):
    - Database schema changes
    - Credential rotation
    - Full platform shutdown
    - Configuration file modifications
    - Any action targeting the database directly
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .models import (
    ClaudeAnalysis,
    IncidentRecord,
    IncidentSeverity,
    PolicyDecision,
    PolicyLevel,
    RecoveryActionType,
)

_log = logging.getLogger("system_x.policy")

# Actions that are always safe to automate (idempotent, reversible, limited blast radius)
_SAFE_ACTIONS = frozenset({
    RecoveryActionType.RESTART_SERVICE,
    RecoveryActionType.CLEAR_CACHE,
    RecoveryActionType.NOTIFY_ONCALL,
})

# Actions requiring human approval at HIGH policy level
_HIGH_RISK_ACTIONS = frozenset({
    RecoveryActionType.FAILOVER,
    RecoveryActionType.SCALE_UP,
    RecoveryActionType.MANUAL_INTERVENTION,
})

# Terms in recovery_plan steps that indicate forbidden operations
_FORBIDDEN_STEP_KEYWORDS = frozenset({
    "database", "schema", "migrate", "drop table", "delete from",
    "credential", "password", "rotate", "shutdown", "terminate all",
    "kill all", "config file", "write config",
})


@dataclass
class PolicyConfig:
    level: PolicyLevel
    dry_run: bool
    max_actions_per_incident: int
    allowed_actions: frozenset[RecoveryActionType]
    require_human_for: frozenset[RecoveryActionType]


_DEFAULT_POLICIES: dict[PolicyLevel, PolicyConfig] = {
    PolicyLevel.LOW: PolicyConfig(
        level=PolicyLevel.LOW,
        dry_run=False,
        max_actions_per_incident=10,
        allowed_actions=frozenset(RecoveryActionType),
        require_human_for=frozenset(),
    ),
    PolicyLevel.MEDIUM: PolicyConfig(
        level=PolicyLevel.MEDIUM,
        dry_run=False,
        max_actions_per_incident=6,
        allowed_actions=frozenset(RecoveryActionType),
        require_human_for=frozenset(),
    ),
    PolicyLevel.HIGH: PolicyConfig(
        level=PolicyLevel.HIGH,
        dry_run=False,
        max_actions_per_incident=4,
        allowed_actions=frozenset(RecoveryActionType),
        require_human_for=_HIGH_RISK_ACTIONS,
    ),
    PolicyLevel.CRITICAL: PolicyConfig(
        level=PolicyLevel.CRITICAL,
        dry_run=False,
        max_actions_per_incident=2,
        allowed_actions=_SAFE_ACTIONS,
        require_human_for=frozenset(RecoveryActionType),
    ),
}


def _severity_to_policy_level(severity: IncidentSeverity) -> PolicyLevel:
    override = os.environ.get("SYSTEM_X_POLICY_LEVEL", "").upper()
    if override and override in PolicyLevel.__members__:
        return PolicyLevel(override)
    if severity == IncidentSeverity.CRITICAL:
        return PolicyLevel.HIGH
    return PolicyLevel.LOW


def _contains_forbidden_operation(plan: tuple[str, ...]) -> str | None:
    """Returns the forbidden keyword if found in any recovery plan step, else None."""
    for step in plan:
        lower = step.lower()
        for kw in _FORBIDDEN_STEP_KEYWORDS:
            if kw in lower:
                return kw
    return None


def _infer_action_types(plan: tuple[str, ...]) -> list[RecoveryActionType]:
    """Map recovery_plan strings to RecoveryActionType for policy evaluation."""
    from .recovery.engine import _infer_action_type
    return [_infer_action_type(step) for step in plan]


class RecoveryPolicyEngine:
    """Evaluates Claude's analysis and produces a PolicyDecision.

    The controller must check this decision before passing anything to the
    RecoveryEngine. If allowed=False, no actions may execute.
    """

    def __init__(self, dry_run: bool = False) -> None:
        self._dry_run_override = dry_run

    def evaluate(
        self,
        incident: IncidentRecord,
        analysis: ClaudeAnalysis,
    ) -> PolicyDecision:
        """Produce a PolicyDecision governing what may execute."""
        level = _severity_to_policy_level(incident.severity)
        config = _DEFAULT_POLICIES[level]
        dry_run = self._dry_run_override or config.dry_run

        # Step 1: check forbidden operations
        forbidden_kw = _contains_forbidden_operation(analysis.recovery_plan)
        if forbidden_kw:
            _log.warning(
                "incident=%s policy BLOCK: forbidden operation '%s' in recovery plan",
                incident.incident_id, forbidden_kw,
            )
            return PolicyDecision(
                allowed=False,
                policy_level=level,
                approved_actions=frozenset(),
                requires_human_approval=True,
                dry_run=dry_run,
                reason=f"Recovery plan contains forbidden operation: '{forbidden_kw}'. Human approval required.",
            )

        # Step 2: check confidence threshold
        if analysis.confidence == "low" and incident.severity == IncidentSeverity.CRITICAL:
            _log.warning(
                "incident=%s policy BLOCK: low confidence on CRITICAL incident",
                incident.incident_id,
            )
            return PolicyDecision(
                allowed=False,
                policy_level=level,
                approved_actions=frozenset(),
                requires_human_approval=True,
                dry_run=dry_run,
                reason=(
                    "Claude's confidence is 'low' on a CRITICAL incident. "
                    "Automated recovery suspended — human review required."
                ),
            )

        # Step 3: determine which inferred actions are approved
        inferred = _infer_action_types(analysis.recovery_plan)
        approved: set[RecoveryActionType] = set()
        needs_human = False
        blocked_actions: list[str] = []

        for action in inferred:
            if action not in config.allowed_actions:
                blocked_actions.append(str(action))
                needs_human = True
            elif action in config.require_human_for:
                needs_human = True
                # Still add to approved so it can execute AFTER human approval
                approved.add(action)
            else:
                approved.add(action)

        # Step 4: cap number of actions
        approved_list = list(approved)[: config.max_actions_per_incident]

        reason_parts = [f"Policy level: {level}"]
        if blocked_actions:
            reason_parts.append(f"Blocked actions (not in policy): {', '.join(blocked_actions)}")
        if needs_human:
            reason_parts.append("One or more actions require human approval at this policy level")
        if dry_run:
            reason_parts.append("DRY RUN MODE — no actions will execute")

        _log.info(
            "incident=%s policy=%s approved=%s requires_human=%s dry_run=%s",
            incident.incident_id, level, [str(a) for a in approved_list], needs_human, dry_run,
        )

        return PolicyDecision(
            allowed=True,
            policy_level=level,
            approved_actions=frozenset(approved_list),
            requires_human_approval=needs_human,
            dry_run=dry_run,
            reason="; ".join(reason_parts),
        )


__all__ = ["RecoveryPolicyEngine"]
