"""PlaybookRegistry — security/privacy/AI incident playbooks (V4 Ch17 §17.12).

Each playbook follows the fixed lifecycle shape: Detect -> Triage -> Contain
-> Eradicate -> Recover -> Notify -> Review (V4 Ch17 §17.12). The registry
holds the six mandatory playbook classes (P1-P6); ``IncidentResponse``
drives the actual state machine (open/execute/notify/close).

Architecture: V4 Ch17 (Incident Response) §17.12 (Algorithms/Policies — playbooks).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IncidentType(StrEnum):
    """The six mandatory incident classes (V4 Ch17 §17.2, §17.12 P1-P6)."""

    SECURITY_BREACH = "SECURITY_BREACH"
    DATA_BREACH = "DATA_BREACH"
    AI_MISBEHAVIOR = "AI_MISBEHAVIOR"
    UNAUTHORIZED_ACCESS = "UNAUTHORIZED_ACCESS"
    COMPLIANCE_VIOLATION = "COMPLIANCE_VIOLATION"
    CREDENTIAL_COMPROMISE = "CREDENTIAL_COMPROMISE"


class Severity(StrEnum):
    """Incident severity (V4 Ch17 §17.12 "sev-1 ... immediate ... lower sevs scaled response")."""

    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"


@dataclass(frozen=True)
class Playbook:
    """A fixed containment/eradication/recovery/notify/review step sequence."""

    incident_type: IncidentType
    steps: tuple[str, ...]


@dataclass(frozen=True)
class PlaybookResult:
    """Outcome of running a playbook against one incident."""

    incident_id: str
    steps_executed: tuple[str, ...]


_DEFAULT_PLAYBOOKS: dict[IncidentType, Playbook] = {
    IncidentType.SECURITY_BREACH: Playbook(
        IncidentType.SECURITY_BREACH,
        (
            "isolate_affected_systems",
            "block_source",
            "patch_or_remove_access",
            "restore_clean_state",
            "notify",
            "review",
        ),
    ),
    IncidentType.DATA_BREACH: Playbook(
        IncidentType.DATA_BREACH,
        ("stop_egress", "revoke_access", "forensics_scope_pii", "notify_dpdp", "close_leak_path"),
    ),
    IncidentType.AI_MISBEHAVIOR: Playbook(
        IncidentType.AI_MISBEHAVIOR,
        (
            "tighten_safety_thresholds",
            "disable_affected_capability",
            "increase_human_oversight",
            "fix_prompt_or_policy",
            "red_team_verify",
        ),
    ),
    IncidentType.UNAUTHORIZED_ACCESS: Playbook(
        IncidentType.UNAUTHORIZED_ACCESS,
        ("revoke_and_invalidate_sessions", "forensics_scope_of_access", "notify_if_data_exposed", "review_authz_gap"),
    ),
    IncidentType.COMPLIANCE_VIOLATION: Playbook(
        IncidentType.COMPLIANCE_VIOLATION,
        ("stop_violating_behavior", "remediate_affected_records", "notify_regulator_if_required", "review_rule_gap"),
    ),
    IncidentType.CREDENTIAL_COMPROMISE: Playbook(
        IncidentType.CREDENTIAL_COMPROMISE,
        (
            "emergency_revoke_and_rotate",
            "invalidate_sessions",
            "forensics_scope",
            "notify_if_data_exposed",
            "review_rotation_hardening",
        ),
    ),
}


class PlaybookRegistry:
    """The registered playbook for each :class:`IncidentType`."""

    def __init__(self, playbooks: dict[IncidentType, Playbook] | None = None) -> None:
        self._playbooks = dict(playbooks) if playbooks is not None else dict(_DEFAULT_PLAYBOOKS)

    def get(self, incident_type: IncidentType) -> Playbook:
        """Return the registered playbook for ``incident_type``."""
        return self._playbooks[incident_type]
