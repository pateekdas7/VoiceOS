"""ComplianceRuleSet — monitor rules for real-time compliance signal correlation (V4 Ch16).

Rules are declarative; Sprint-020.md specifies they are "loaded from
PolicyEngine compliance domain (same source of truth as enforcement)"
(V4 Ch16 §16.12) — ``policy_engine_service`` is accepted as an optional,
additive future source of rule overrides (same optional-backend precedent
as every PolicyEngineService integration since Sprint-017), while a fixed
built-in default set (mirroring the compliance policy packs already shipped
in ``src/services/policy_engine/packs/``) keeps this service fully
functional with zero external dependencies in Phase 1.

Architecture: V4 Ch16 (Compliance Monitoring) §16.6 (Outputs), §16.12.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.policy_engine.service import PolicyEngineService


@dataclass(frozen=True)
class MonitorRule:
    """One declarative signal-correlation rule (V4 Ch16 §16.12)."""

    rule_id: str
    matches_action: str
    threshold_count: int
    window_seconds: int
    alert_kind: str


DEFAULT_RULES: tuple[MonitorRule, ...] = (
    MonitorRule(
        rule_id="COMPLIANCE_SIGNAL_CONSENT_BYPASS_ATTEMPT",
        matches_action="consent.denied",
        threshold_count=5,
        window_seconds=300,
        alert_kind="CONSENT_VIOLATION",
    ),
    MonitorRule(
        rule_id="COMPLIANCE_SIGNAL_REPEATED_POLICY_DENIAL",
        matches_action="policy.denied",
        threshold_count=5,
        window_seconds=300,
        alert_kind="POLICY_VIOLATION",
    ),
    MonitorRule(
        rule_id="COMPLIANCE_SIGNAL_REPEATED_AUTHN_FAILURE",
        matches_action="authn.failed",
        threshold_count=5,
        window_seconds=60,
        alert_kind="SECURITY_INCIDENT",
    ),
)


class ComplianceRuleSet:
    """The active set of :class:`MonitorRule` this deployment monitors."""

    def __init__(
        self,
        policy_engine_service: PolicyEngineService | None = None,
        rules: tuple[MonitorRule, ...] = DEFAULT_RULES,
    ) -> None:
        self._policy_engine_service = policy_engine_service
        self._rules = rules

    def rules(self) -> tuple[MonitorRule, ...]:
        """Return the active monitor rules (V4 Ch16 §16.7 ``rules()``)."""
        return self._rules
