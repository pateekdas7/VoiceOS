"""Role and the role→permission mapping (V4 Ch6 §6.3 "Roles and Permissions").

Architecture: V4 Ch6 (Authorization/RBAC).
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """The five VoiceOS RBAC roles (V4 Ch6 §6.3)."""

    ADMIN = "ADMIN"
    """Full CRUD on all resources within tenant."""

    SUPERVISOR = "SUPERVISOR"
    """Read all, write campaigns/users, monitor calls, barge-in."""

    MANAGER = "MANAGER"
    """Read all, write campaigns, view analytics."""

    AGENT = "AGENT"
    """Handle escalated calls only (human agent context)."""

    AUDITOR = "AUDITOR"
    """Read-only audit log + transcripts."""


# Permission vocabulary. Names are deliberately coarse-grained action verbs +
# resource classes (V4 Ch6 §6.4) — fine-grained per-resource ABAC scoping is
# layered on top by ABACEvaluator, not encoded here.
PERM_READ_ALL = "read:all"
PERM_WRITE_CAMPAIGNS = "write:campaigns"
PERM_WRITE_USERS = "write:users"
PERM_MONITOR_CALLS = "monitor:calls"
PERM_BARGE_IN = "barge_in:calls"
PERM_VIEW_ANALYTICS = "view:analytics"
PERM_HANDLE_ESCALATED_CALLS = "handle:escalated_calls"
PERM_READ_AUDIT = "read:audit"
PERM_READ_TRANSCRIPTS = "read:transcripts"
PERM_WRITE_ALL = "write:all"
PERM_DECIDE_HITL = "decide:hitl_items"
"""V4 Ch15: authority to record a decision (approve/reject/override) on a HITL queue item."""

ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.ADMIN: frozenset({PERM_READ_ALL, PERM_WRITE_ALL, PERM_WRITE_CAMPAIGNS, PERM_WRITE_USERS, PERM_DECIDE_HITL}),
    Role.SUPERVISOR: frozenset(
        {
            PERM_READ_ALL,
            PERM_WRITE_CAMPAIGNS,
            PERM_WRITE_USERS,
            PERM_MONITOR_CALLS,
            PERM_BARGE_IN,
            PERM_DECIDE_HITL,
        }
    ),
    Role.MANAGER: frozenset({PERM_READ_ALL, PERM_WRITE_CAMPAIGNS, PERM_VIEW_ANALYTICS}),
    Role.AGENT: frozenset({PERM_HANDLE_ESCALATED_CALLS}),
    Role.AUDITOR: frozenset({PERM_READ_AUDIT, PERM_READ_TRANSCRIPTS}),
}
"""Role -> the frozen set of permissions it holds (V4 Ch6 §6.3 table).

Deliberately does not include a blanket ``PERM_READ_ALL`` write-side
implication: AUDITOR (and AGENT) never gain any ``write:*`` permission
regardless of what they can read — the RBAC engine's write/read
distinction (see :mod:`rbac_engine`) enforces this mechanically, not just
by table construction, so a future role addition can't silently regress it.
"""

WRITE_PERMISSIONS = frozenset({PERM_WRITE_ALL, PERM_WRITE_CAMPAIGNS, PERM_WRITE_USERS})
"""Permissions considered "write" actions for the HTTP-verb-based RBAC check
(POST/PUT/PATCH/DELETE) — see :meth:`rbac_engine.RBACEngine.check_http_method`."""
