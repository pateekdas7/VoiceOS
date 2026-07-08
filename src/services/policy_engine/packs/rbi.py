"""RBIPolicyPack — RBI Fair Practice Code collections rules (V4 Ch2 §"RBI collections").

"rules encoding permitted call windows/frequency (no harassment), mandatory
identification/disclosure, prohibition of threats/coercion."

Every rule here is a hard rule (``hard_rule=True``): DPDP/RBI/AI-governance
rules are structurally unweakenable by tenant/campaign scopes (V4 Ch4 §4.13
``hard_rules_unweakenable: [dpdp, rbi, law_of_authority, encryption]``).

Context keys consumed:
    hour (int, 0-23): local hour of the call attempt. Default 12 (safe/inside window).
    calls_today_count (int): calls already placed to this customer today. Default 0.
    utterance_classification (str): e.g. 'abusive', 'threatening', 'neutral'.
    identity_verified (bool): whether the customer's identity was verified this call.
    turn_index (int): zero-based turn index (0 = call start).
    disclosure_given (bool): whether agent-identity/purpose disclosure has been made.
    recording_consent (bool): whether recording consent has been captured.

Rule-to-action scoping: ``CALLING_HOURS``/``CALLING_FREQUENCY`` govern whether
a call may be dialed at all and apply regardless of ``action``.
``IDENTITY_VERIFY_FIRST`` applies only to ``action="disclose_debt"``.
``DISCLOSURE_REQUIRED``/``RECORDING_CONSENT`` govern the call-opening script
and apply only to ``action="start_call"`` — so a pure pre-dial admission
check (a different action, e.g. ``"admit_call"``) is judged solely on
calling-hours/frequency, not on script obligations that are only meaningful
once the call actually connects.

Architecture: V4 Ch2 (Regulatory Compliance — RBI); V4 Ch4.
"""

from __future__ import annotations

from ..decision import PolicyOutcome
from ..rule import PolicyCondition, PolicyEffect, PolicyRequest, PolicyRule

CALLING_WINDOW_START_HOUR = 8
CALLING_WINDOW_END_HOUR = 20
MAX_CALLS_PER_DAY = 3


def _outside_calling_hours(request: PolicyRequest) -> bool:
    hour = request.context.get("hour", 12)
    return not (CALLING_WINDOW_START_HOUR <= hour < CALLING_WINDOW_END_HOUR)


def _frequency_exceeded(request: PolicyRequest) -> bool:
    return bool(request.context.get("calls_today_count", 0) >= MAX_CALLS_PER_DAY)


def _abusive_utterance(request: PolicyRequest) -> bool:
    return request.context.get("utterance_classification") in ("abusive", "threatening")


def _disclosure_required_before_debt(request: PolicyRequest) -> bool:
    return request.action == "disclose_debt" and not request.context.get("identity_verified", False)


def _disclosure_not_yet_given(request: PolicyRequest) -> bool:
    return (
        request.action == "start_call"
        and request.context.get("turn_index", 0) == 0
        and not request.context.get("disclosure_given", False)
    )


def _recording_consent_missing(request: PolicyRequest) -> bool:
    return request.action == "start_call" and not request.context.get("recording_consent", False)


class RBIPolicyPack:
    """RBI Fair Practice Code rules (V4 Ch2, V4 Ch4).

    Each rule is exposed as a class attribute so it can be evaluated
    directly (``RBIPolicyPack.CALLING_HOURS.decide(request)``) or composed
    into a :class:`~src.services.policy_engine.policy_set.PolicySet` via
    :meth:`policy_set`.
    """

    CALLING_HOURS: PolicyRule = PolicyRule(
        rule_id="RBI-CALLING-HOURS",
        pack="rbi",
        domain="rbi",
        condition=PolicyCondition(
            f"call attempted outside {CALLING_WINDOW_START_HOUR:02d}:00-{CALLING_WINDOW_END_HOUR:02d}:00 local time",
            _outside_calling_hours,
        ),
        effect=PolicyEffect(PolicyOutcome.DENY),
        description="RBI Fair Practice Code: calls to customers are only permitted 08:00-20:00 local time.",
        hard_rule=True,
    )

    CALLING_FREQUENCY: PolicyRule = PolicyRule(
        rule_id="RBI-CALLING-FREQUENCY",
        pack="rbi",
        domain="rbi",
        condition=PolicyCondition(
            f"calls_today_count >= {MAX_CALLS_PER_DAY}",
            _frequency_exceeded,
        ),
        effect=PolicyEffect(PolicyOutcome.DENY),
        description="RBI Fair Practice Code: no more than 3 calls to the same customer per day.",
        hard_rule=True,
    )

    ABUSE_PROHIBITION: PolicyRule = PolicyRule(
        rule_id="RBI-ABUSE-PROHIBITION",
        pack="rbi",
        domain="rbi",
        condition=PolicyCondition(
            "utterance classified as threatening or abusive",
            _abusive_utterance,
        ),
        effect=PolicyEffect(PolicyOutcome.FORBID),
        description="RBI Fair Practice Code Clause 5: threatening or abusive language is absolutely prohibited.",
        hard_rule=True,
    )

    IDENTITY_VERIFY_FIRST: PolicyRule = PolicyRule(
        rule_id="RBI-IDENTITY-VERIFY-FIRST",
        pack="rbi",
        domain="rbi",
        condition=PolicyCondition(
            "debt disclosure attempted before identity verification",
            _disclosure_required_before_debt,
        ),
        effect=PolicyEffect(PolicyOutcome.REQUIRE, obligations=("verify_identity",)),
        description="Identity must be verified before any debt/account disclosure.",
        hard_rule=True,
    )

    DISCLOSURE_REQUIRED: PolicyRule = PolicyRule(
        rule_id="RBI-DISCLOSURE-REQUIRED",
        pack="rbi",
        domain="rbi",
        condition=PolicyCondition(
            "agent identity and call purpose not yet disclosed at call start",
            _disclosure_not_yet_given,
        ),
        effect=PolicyEffect(PolicyOutcome.REQUIRE, obligations=("disclose_agent_identity", "disclose_call_purpose")),
        description="Agent identity and call purpose must be disclosed at call start.",
        hard_rule=True,
    )

    RECORDING_CONSENT: PolicyRule = PolicyRule(
        rule_id="RBI-RECORDING-CONSENT",
        pack="rbi",
        domain="rbi",
        condition=PolicyCondition(
            "call recording consent not yet captured",
            _recording_consent_missing,
        ),
        effect=PolicyEffect(PolicyOutcome.REQUIRE, obligations=("capture_recording_consent",)),
        description="Recording consent must be captured before the call proceeds.",
        hard_rule=True,
    )

    ALL_RULES: tuple[PolicyRule, ...] = (
        CALLING_HOURS,
        CALLING_FREQUENCY,
        ABUSE_PROHIBITION,
        IDENTITY_VERIFY_FIRST,
        DISCLOSURE_REQUIRED,
        RECORDING_CONSENT,
    )

    @classmethod
    def rules(cls) -> tuple[PolicyRule, ...]:
        """All RBI rules, for registration into a PolicyEngine's rule registry."""
        return cls.ALL_RULES
