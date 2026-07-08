"""Unit tests for DialoguePolicyEngine.

Covers mandatory constraints, conditional constraints, and acceptance criteria.
Architecture: V2 Ch8.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.engines.dialogue_policy.constraints import PolicyConstraintType
from src.engines.dialogue_policy.engine import DialoguePolicyEngine, PolicyLookupPort
from src.engines.risk.flags import RiskFlag
from src.engines.risk.result import RiskAssessment
from src.libs.contracts.context import (
    ConsentStatus,
    ContactInfo,
    CustomerContext,
    OutstandingBalance,
    PartyInfo,
)
from src.libs.contracts.primitives import Currency, CustomerId, Money, TenantId
from src.libs.contracts.turn import TurnInput, TurnRole, UtteranceSegment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_turn(transcript: str = "Theek hai", turn_id: str = "t-001") -> TurnInput:
    return TurnInput(
        turn_id=turn_id,
        call_id="call-001",
        tenant_id="tenant-001",
        role=TurnRole.CUSTOMER,
        transcript=transcript,
        segments=(UtteranceSegment(text=transcript, start_ms=0, end_ms=1000, confidence=0.9),),
        created_at=datetime.now(tz=UTC),
        correlation_id="corr-001",
        trace_id="trace-001",
        turn_index=0,
    )


def _make_context() -> CustomerContext:
    return CustomerContext(
        customer_id=CustomerId("cust-001"),
        tenant_id=TenantId("tenant-001"),
        primary_party=PartyInfo(
            party_id=CustomerId("cust-001"),
            role="BORROWER",
            name="Test Customer",
            contact=ContactInfo(phone_number="+919876543210"),  # type: ignore[arg-type]
        ),
        outstanding=OutstandingBalance(
            total_outstanding=Money(amount_minor=500000, currency=Currency.INR),
            total_overdue=Money(amount_minor=200000, currency=Currency.INR),
            account_count=1,
        ),
        consent_status=ConsentStatus.GRANTED,
    )


def _make_risk(flags: list[RiskFlag], human_handoff: bool = False) -> RiskAssessment:
    return RiskAssessment(
        flags=flags,
        escalation_required=any(
            f in (RiskFlag.ABUSE_DETECTED, RiskFlag.LEGAL_THREAT, RiskFlag.ESCALATION_TRIGGER) for f in flags
        ),
        human_handoff_required=human_handoff,
    )


@pytest.fixture
def engine() -> DialoguePolicyEngine:
    return DialoguePolicyEngine()


# ---------------------------------------------------------------------------
# AC: MUST_NOT_THREATEN is always present
# ---------------------------------------------------------------------------


def test_must_not_threaten_always_present(engine: DialoguePolicyEngine) -> None:
    """MUST_NOT_THREATEN is added to every call regardless of other signals."""
    turn = _make_turn()
    constraints = engine.evaluate(turn)
    assert PolicyConstraintType.MUST_NOT_THREATEN in constraints


def test_must_not_threaten_present_with_no_risk(engine: DialoguePolicyEngine) -> None:
    """No risk, no context → MUST_NOT_THREATEN still present."""
    turn = _make_turn()
    constraints = engine.evaluate(turn, risk=None, context=None)
    assert PolicyConstraintType.MUST_NOT_THREATEN in constraints


def test_must_not_threaten_present_on_high_stress(engine: DialoguePolicyEngine) -> None:
    turn = _make_turn("Bahut tension hai")
    risk = _make_risk([RiskFlag.ESCALATION_TRIGGER])
    constraints = engine.evaluate(turn, risk=risk)
    assert PolicyConstraintType.MUST_NOT_THREATEN in constraints


# ---------------------------------------------------------------------------
# Recording disclosure
# ---------------------------------------------------------------------------


def test_recording_disclosure_on_first_turn(engine: DialoguePolicyEngine) -> None:
    """Turn index 0 → MUST_DISCLOSE_RECORDING."""
    turn = _make_turn()
    constraints = engine.evaluate(turn, turn_index=0)
    assert PolicyConstraintType.MUST_DISCLOSE_RECORDING in constraints


def test_recording_disclosure_not_on_later_turns(engine: DialoguePolicyEngine) -> None:
    """Turn index > 0 without recording objection → no disclosure constraint."""
    turn = _make_turn()
    constraints = engine.evaluate(turn, turn_index=3)
    assert PolicyConstraintType.MUST_DISCLOSE_RECORDING not in constraints


def test_recording_disclosure_on_objection(engine: DialoguePolicyEngine) -> None:
    """Recording objection at any turn → MUST_DISCLOSE_RECORDING."""
    turn = _make_turn("Stop recording")
    risk = _make_risk([RiskFlag.RECORDING_OBJECTION])
    constraints = engine.evaluate(turn, risk=risk, turn_index=5)
    assert PolicyConstraintType.MUST_DISCLOSE_RECORDING in constraints


# ---------------------------------------------------------------------------
# Identity verification
# ---------------------------------------------------------------------------


def test_identity_verify_constraint_unverified(engine: DialoguePolicyEngine) -> None:
    """Unverified identity → MUST_VERIFY_IDENTITY_BEFORE_DISCLOSURE."""
    turn = _make_turn()
    constraints = engine.evaluate(turn, identity_verified=False)
    assert PolicyConstraintType.MUST_VERIFY_IDENTITY_BEFORE_DISCLOSURE in constraints


def test_identity_verify_constraint_verified(engine: DialoguePolicyEngine) -> None:
    """Verified identity → no verification constraint."""
    turn = _make_turn()
    constraints = engine.evaluate(turn, identity_verified=True)
    assert PolicyConstraintType.MUST_VERIFY_IDENTITY_BEFORE_DISCLOSURE not in constraints


# ---------------------------------------------------------------------------
# Amount / DPD accuracy
# ---------------------------------------------------------------------------


def test_amount_accuracy_with_context(engine: DialoguePolicyEngine) -> None:
    """With CustomerContext → MUST_NOT_MISREPRESENT_AMOUNT added."""
    turn = _make_turn()
    ctx = _make_context()
    constraints = engine.evaluate(turn, context=ctx)
    assert PolicyConstraintType.MUST_NOT_MISREPRESENT_AMOUNT in constraints
    assert PolicyConstraintType.MUST_REFERENCE_DPD_CORRECTLY in constraints


def test_amount_accuracy_without_context(engine: DialoguePolicyEngine) -> None:
    """Without CustomerContext → no amount/DPD constraints."""
    turn = _make_turn()
    constraints = engine.evaluate(turn, context=None)
    assert PolicyConstraintType.MUST_NOT_MISREPRESENT_AMOUNT not in constraints


# ---------------------------------------------------------------------------
# DND / Consent risk
# ---------------------------------------------------------------------------


def test_dnd_constraint_on_consent_risk(engine: DialoguePolicyEngine) -> None:
    """CONSENT_RISK flag → MUST_RESPECT_DND constraint added."""
    turn = _make_turn("Band karo calls")
    risk = _make_risk([RiskFlag.CONSENT_RISK])
    constraints = engine.evaluate(turn, risk=risk)
    assert PolicyConstraintType.MUST_RESPECT_DND in constraints


def test_no_dnd_without_consent_risk(engine: DialoguePolicyEngine) -> None:
    """No CONSENT_RISK → no DND constraint."""
    turn = _make_turn()
    constraints = engine.evaluate(turn, risk=_make_risk([]))
    assert PolicyConstraintType.MUST_RESPECT_DND not in constraints


# ---------------------------------------------------------------------------
# Harassment
# ---------------------------------------------------------------------------


def test_must_not_harass_always_present(engine: DialoguePolicyEngine) -> None:
    """MUST_NOT_HARASS is mandatory on every call."""
    turn = _make_turn()
    constraints = engine.evaluate(turn)
    assert PolicyConstraintType.MUST_NOT_HARASS in constraints


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_deterministic_same_inputs(engine: DialoguePolicyEngine) -> None:
    """Same inputs → same constraint list."""
    turn = _make_turn()
    ctx = _make_context()
    risk = _make_risk([RiskFlag.RECORDING_OBJECTION, RiskFlag.CONSENT_RISK])
    c1 = engine.evaluate(turn, risk=risk, context=ctx, turn_index=0, identity_verified=False)
    c2 = engine.evaluate(turn, risk=risk, context=ctx, turn_index=0, identity_verified=False)
    assert c1 == c2


def test_return_type_is_list(engine: DialoguePolicyEngine) -> None:
    turn = _make_turn()
    result = engine.evaluate(turn)
    assert isinstance(result, list)
    assert all(isinstance(c, PolicyConstraintType) for c in result)


# ---------------------------------------------------------------------------
# Sprint-017: PolicyLookupPort live-rule hook
# ---------------------------------------------------------------------------


class _StubPolicyLookup:
    """Boundary-safe PolicyLookupPort test double — no src.services import."""

    def __init__(self, outcome: str) -> None:
        self._outcome = outcome
        self.calls: list[tuple[str, dict[str, object]]] = []

    def check_conversational_rule(self, rule_id: str, context: dict[str, object]) -> str:
        self.calls.append((rule_id, context))
        return self._outcome


def test_policy_lookup_none_preserves_pre_sprint_017_behavior(engine: DialoguePolicyEngine) -> None:
    """No policy_lookup wired -> later-turn recording disclosure unchanged (absent)."""
    turn = _make_turn()
    constraints = engine.evaluate(turn, turn_index=3, policy_lookup=None)
    assert PolicyConstraintType.MUST_DISCLOSE_RECORDING not in constraints


def test_policy_lookup_require_adds_recording_disclosure_beyond_turn_zero(engine: DialoguePolicyEngine) -> None:
    """A live PDP REQUIRE on RBI-RECORDING-CONSENT adds the constraint even past turn 0."""
    stub = _StubPolicyLookup(outcome="REQUIRE")
    turn = _make_turn()
    constraints = engine.evaluate(turn, turn_index=3, recording_consent=False, policy_lookup=stub)
    assert PolicyConstraintType.MUST_DISCLOSE_RECORDING in constraints
    assert stub.calls == [("RBI-RECORDING-CONSENT", {"recording_consent": False, "turn_index": 3})]


def test_policy_lookup_permit_does_not_add_recording_disclosure(engine: DialoguePolicyEngine) -> None:
    stub = _StubPolicyLookup(outcome="PERMIT")
    turn = _make_turn()
    constraints = engine.evaluate(turn, turn_index=3, recording_consent=True, policy_lookup=stub)
    assert PolicyConstraintType.MUST_DISCLOSE_RECORDING not in constraints


def test_policy_lookup_not_consulted_at_turn_zero(engine: DialoguePolicyEngine) -> None:
    """Turn 0 already adds the constraint via the local heuristic — the live hook is skipped."""
    stub = _StubPolicyLookup(outcome="PERMIT")
    turn = _make_turn()
    constraints = engine.evaluate(turn, turn_index=0, policy_lookup=stub)
    assert PolicyConstraintType.MUST_DISCLOSE_RECORDING in constraints
    assert stub.calls == []


def test_stub_policy_lookup_satisfies_protocol() -> None:
    assert isinstance(_StubPolicyLookup(outcome="PERMIT"), PolicyLookupPort)
