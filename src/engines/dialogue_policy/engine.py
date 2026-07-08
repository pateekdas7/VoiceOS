"""DialoguePolicyEngine — hard compliance constraint evaluation.

Applies RBI Fair Practice Code and DPDP-derived hard rules to every turn,
producing a list of PolicyConstraintTypes that the ResponsePlan must honour.
All rules are deterministic; no LLM is invoked here.

Sprint-017 (Policy Engine, ``src/services/policy_engine/``) added the central
enterprise PDP. This module still ships its own always-applicable local
rule set as the mandatory, infrastructure-free baseline (unchanged
behavior); ``policy_lookup`` (below) is a boundary-safe hook a composition
root may wire to a live PDP for additional, per-tenant/per-campaign
constraints — this module still never imports ``src/services/``
(check_boundaries.py Rule 2), so the hook is a structural Protocol rather
than a concrete PolicyEngineService import.

Architecture: V2 Ch8 (Dialogue Policy Engine); V4 Ch4 (Policy Engine).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from src.libs.contracts.context import CustomerContext
from src.libs.contracts.turn import TurnInput

from ..risk.flags import RiskFlag
from ..risk.result import RiskAssessment
from .constraints import PolicyConstraintType

logger = logging.getLogger(__name__)


@runtime_checkable
class PolicyLookupPort(Protocol):
    """Structural port for live policy consultation (Sprint-017).

    Deliberately typed with only primitives (``str``/``dict``) — this module
    must not import ``src/services/policy_engine`` (boundary Rule 2). Any
    object exposing this method (e.g. an adapter around
    ``PolicyEngineService.check_conversational_rule``) satisfies the port
    without this module ever importing the services layer.
    """

    def check_conversational_rule(self, rule_id: str, context: dict[str, Any]) -> str:
        """Return the live outcome ('PERMIT'|'DENY'|'REQUIRE'|'FORBID') for ``rule_id``."""
        ...


_RECORDING_CONSENT_RULE_ID = "RBI-RECORDING-CONSENT"


class DialoguePolicyEngine:
    """Evaluates compliance constraints for a single dialogue turn.

    Produces a list of PolicyConstraintTypes that must be applied to the
    ResponsePlan. Constraints are additive: the presence of one constraint
    does not remove another.

    Architecture: V2 Ch8.
    """

    def evaluate(
        self,
        turn: TurnInput,
        risk: RiskAssessment | None = None,
        context: CustomerContext | None = None,
        turn_index: int = 0,
        identity_verified: bool = False,
        recording_consent: bool = False,
        policy_lookup: PolicyLookupPort | None = None,
    ) -> list[PolicyConstraintType]:
        """Evaluate applicable policy constraints for this turn.

        Args:
            turn: Finalized TurnInput.
            risk: Risk assessment from RiskEngine (optional; used to add
                  risk-driven constraints).
            context: CustomerContext snapshot (optional; used for identity
                     verification and DPD checks).
            turn_index: Zero-based index of this turn (0 = first customer turn).
                        Used to apply one-time disclosures.
            identity_verified: Whether the customer's identity has been verified
                               this session. Drives MUST_VERIFY_IDENTITY_BEFORE_DISCLOSURE.
            recording_consent: Whether recording consent has been captured this
                                session. Consulted only when ``policy_lookup`` is wired.
            policy_lookup: Optional live PDP consultation hook (Sprint-017). When
                           provided, this turn's recording-consent constraint is
                           additionally re-checked against the central Policy
                           Engine (RBI-RECORDING-CONSENT) instead of only the
                           turn-0 local heuristic below. ``None`` preserves
                           pre-Sprint-017 behavior exactly.

        Returns:
            List of PolicyConstraintType values active for this turn.
        """
        constraints: list[PolicyConstraintType] = []

        # MUST_NOT_THREATEN is mandatory on every call, no exceptions (RBI FPC).
        constraints.append(PolicyConstraintType.MUST_NOT_THREATEN)

        # MUST_NOT_HARASS is mandatory on every call.
        constraints.append(PolicyConstraintType.MUST_NOT_HARASS)

        # Recording disclosure — required at call start (turn 0) and if the
        # customer raises a recording objection at any turn.
        if turn_index == 0 or (risk is not None and RiskFlag.RECORDING_OBJECTION in risk.flags):
            constraints.append(PolicyConstraintType.MUST_DISCLOSE_RECORDING)
        elif policy_lookup is not None:
            # Sprint-017 live-rule hook: beyond turn 0, the central Policy
            # Engine may still require recording consent (e.g. a mid-call
            # tenant/campaign override) even though the local turn-0
            # heuristic above no longer applies.
            live_outcome = policy_lookup.check_conversational_rule(
                _RECORDING_CONSENT_RULE_ID,
                {"recording_consent": recording_consent, "turn_index": turn_index},
            )
            if live_outcome == "REQUIRE":
                constraints.append(PolicyConstraintType.MUST_DISCLOSE_RECORDING)

        # Identity verification gate — must verify before disclosing account details.
        if not identity_verified:
            constraints.append(PolicyConstraintType.MUST_VERIFY_IDENTITY_BEFORE_DISCLOSURE)

        # Amount accuracy constraint — always active when context is present.
        if context is not None:
            constraints.append(PolicyConstraintType.MUST_NOT_MISREPRESENT_AMOUNT)
            constraints.append(PolicyConstraintType.MUST_REFERENCE_DPD_CORRECTLY)

        # DND respect — add when consent risk is raised.
        if risk is not None and RiskFlag.CONSENT_RISK in risk.flags:
            constraints.append(PolicyConstraintType.MUST_RESPECT_DND)

        logger.debug(
            "Policy constraints evaluated",
            extra={
                "turn_id": turn.turn_id,
                "call_id": turn.call_id,
                "turn_index": turn_index,
                "constraints": [c.value for c in constraints],
            },
        )

        return constraints
