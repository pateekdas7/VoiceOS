"""Unit tests for Sprint-029 Founder Validation Suite (Phase 1).

Tests all four automated checkers against:
  - Synthetic fixture files in evaluation/call-samples/synthetic/
  - Programmatically constructed transcript objects for edge-case coverage

Phase-2 section verifies human-review dimensions remain PENDING (None/False)
and are never fabricated.

Architecture: Sprint-029.md; DocSuite-10 (AI Evaluation Handbook).
"""

from __future__ import annotations

import json
import pydantic
import pytest
from pathlib import Path

_PYDANTIC_V2 = int(pydantic.VERSION.split(".")[0]) >= 2
requires_pydantic_v2 = pytest.mark.skipif(
    not _PYDANTIC_V2,
    reason="Requires pydantic>=2.7 (see pyproject.toml). Install: pip install 'pydantic>=2.7'",
)

from tests.ai_eval.founder_validation_suite import (
    CallTranscript,
    CallEvalResult,
    TranscriptCustomerContext,
    TranscriptTurn,
    LawOfAuthorityReplayChecker,
    RBIComplianceChecker,
    NegotiationEnvelopeChecker,
    IntentAccuracyEvaluator,
    IntentEvalResult,
    FounderValidationSuite,
    FounderValidationReport,
    Violation,
)
# IntentModel imported lazily inside tests that need it (requires pydantic>=2.7)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_SYNTHETIC_DIR = _PROJECT_ROOT / "evaluation/call-samples/synthetic"

_GOOD_CALL_FIXTURE = _SYNTHETIC_DIR / "good_call_001.json"
_LOA_VIOLATION_FIXTURE = _SYNTHETIC_DIR / "loa_violation_001.json"
_RBI_HOURS_FIXTURE = _SYNTHETIC_DIR / "rbi_calling_hours_violation_001.json"
_RBI_FREQ_FIXTURE = _SYNTHETIC_DIR / "rbi_frequency_violation_001.json"
_NEG_FLOOR_FIXTURE = _SYNTHETIC_DIR / "negotiation_floor_violation_001.json"


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _ctx(
    outstanding: float = 50000.0,
    settlement_pct: float = 0.5,
    loan_id: str = "LOAN-TEST-001",
    due_date: str = "2026-08-01",
    calls_today: int = 0,
    call_hour: int = 14,
) -> TranscriptCustomerContext:
    return TranscriptCustomerContext(
        outstanding_amount_inr=outstanding,
        outstanding_amount_minor=int(outstanding * 100),
        minimum_settlement_pct=settlement_pct,
        loan_id=loan_id,
        due_date=due_date,
        calls_today_count=calls_today,
        call_hour=call_hour,
    )


def _transcript(
    call_id: str,
    ctx: TranscriptCustomerContext,
    turns: list[TranscriptTurn],
    completed: bool = True,
) -> CallTranscript:
    return CallTranscript(
        call_id=call_id,
        customer_context=ctx,
        turns=turns,
        completed=completed,
    )


def _agent_turn(
    index: int,
    text: str,
    offer: float | None = None,
) -> TranscriptTurn:
    return TranscriptTurn(
        turn_index=index,
        speaker="agent",
        text=text,
        negotiation_offer_inr=offer,
    )


def _customer_turn(
    index: int,
    text: str,
    intent: str | None = None,
    offer: float | None = None,
) -> TranscriptTurn:
    return TranscriptTurn(
        turn_index=index,
        speaker="customer",
        text=text,
        human_intent=intent,
        negotiation_offer_inr=offer,
    )


# ---------------------------------------------------------------------------
# Stub IntentAccuracyEvaluator for FounderValidationSuite injection
# ---------------------------------------------------------------------------

class _FixedIntentEvaluator(IntentAccuracyEvaluator):
    """Returns a pre-built IntentEvalResult regardless of input transcripts."""

    def __init__(self, result: IntentEvalResult) -> None:
        self._fixed_result = result

    def evaluate_transcripts(self, transcripts: list[CallTranscript]) -> IntentEvalResult:
        return self._fixed_result


# ---------------------------------------------------------------------------
# CallTranscript.from_json
# ---------------------------------------------------------------------------

class TestCallTranscriptFromJson:
    def test_good_call_loads_correctly(self) -> None:
        t = CallTranscript.from_json(_GOOD_CALL_FIXTURE)
        assert t.call_id == "synthetic-good-001"
        assert t.completed is True
        assert t.customer_context.outstanding_amount_inr == 50000.0
        assert t.customer_context.loan_id == "LOAN-20240112-001"
        assert t.customer_context.call_hour == 14
        assert t.customer_context.calls_today_count == 1
        assert len(t.turns) == 6

    def test_loa_violation_loads_correctly(self) -> None:
        t = CallTranscript.from_json(_LOA_VIOLATION_FIXTURE)
        assert t.call_id == "synthetic-loa-violation-001"
        assert t.completed is False
        assert t.customer_context.outstanding_amount_inr == 50000.0

    def test_rbi_hours_violation_loads_correctly(self) -> None:
        t = CallTranscript.from_json(_RBI_HOURS_FIXTURE)
        assert t.customer_context.call_hour == 7
        assert t.customer_context.calls_today_count == 0

    def test_rbi_frequency_violation_loads_correctly(self) -> None:
        t = CallTranscript.from_json(_RBI_FREQ_FIXTURE)
        assert t.customer_context.calls_today_count == 3

    def test_negotiation_floor_violation_loads_correctly(self) -> None:
        t = CallTranscript.from_json(_NEG_FLOOR_FIXTURE)
        agent_offers = [
            turn.negotiation_offer_inr
            for turn in t.turns
            if turn.negotiation_offer_inr is not None
        ]
        assert len(agent_offers) == 1
        assert agent_offers[0] == 10000.0

    def test_invalid_json_raises_value_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{broken json}", encoding="utf-8")
        with pytest.raises(ValueError, match="Failed to load transcript"):
            CallTranscript.from_json(bad)

    def test_missing_required_field_raises_value_error(self, tmp_path: Path) -> None:
        data = {"call_id": "x", "customer_context": {}, "turns": [], "completed": True}
        bad = tmp_path / "missing.json"
        bad.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="Failed to load transcript"):
            CallTranscript.from_json(bad)


# ---------------------------------------------------------------------------
# LawOfAuthorityReplayChecker
# ---------------------------------------------------------------------------

class TestLawOfAuthorityReplayChecker:
    @pytest.fixture(autouse=True)
    def checker(self) -> LawOfAuthorityReplayChecker:
        self._checker = LawOfAuthorityReplayChecker()
        return self._checker

    def test_good_call_no_violations(self) -> None:
        t = CallTranscript.from_json(_GOOD_CALL_FIXTURE)
        assert self._checker.check_transcript(t) == []

    def test_loa_violation_fixture_two_violations(self) -> None:
        t = CallTranscript.from_json(_LOA_VIOLATION_FIXTURE)
        violations = self._checker.check_transcript(t)
        assert len(violations) == 2
        for v in violations:
            assert v.checker == "LawOfAuthority"
            assert "75,000" in v.description or "75000" in v.description

    def test_invented_amount_caught(self) -> None:
        t = _transcript(
            "test-loa-1",
            _ctx(outstanding=10000.0),
            [_agent_turn(0, "Aapka bakaya Rs. 99,000 hai.")],
        )
        violations = self._checker.check_transcript(t)
        assert len(violations) == 1
        assert violations[0].checker == "LawOfAuthority"
        assert violations[0].turn_index == 0
        assert "99000" in violations[0].description.replace(",", "")

    def test_correct_outstanding_amount_passes(self) -> None:
        t = _transcript(
            "test-loa-2",
            _ctx(outstanding=10000.0),
            [_agent_turn(0, "Aapka bakaya Rs. 10,000 hai.")],
        )
        assert self._checker.check_transcript(t) == []

    def test_floor_amount_is_authoritative(self) -> None:
        # Floor = 50% of 10,000 = 5,000 — agent citing floor is authorized
        t = _transcript(
            "test-loa-3",
            _ctx(outstanding=10000.0, settlement_pct=0.5),
            [_agent_turn(0, "Hum Rs. 5,000 mein settle kar sakte hain.")],
        )
        assert self._checker.check_transcript(t) == []

    def test_amount_below_floor_not_authorized(self) -> None:
        # Rs. 3,000 is neither outstanding (10,000) nor floor (5,000)
        t = _transcript(
            "test-loa-4",
            _ctx(outstanding=10000.0, settlement_pct=0.5),
            [_agent_turn(0, "Hum Rs. 3,000 mein settle kar sakte hain.")],
        )
        violations = self._checker.check_transcript(t)
        assert len(violations) == 1

    def test_invented_loan_id_caught(self) -> None:
        t = _transcript(
            "test-loa-5",
            _ctx(loan_id="LOAN-TEST-001"),
            [_agent_turn(0, "Aapka loan LOAN-FAKE-999999999 hai.")],
        )
        violations = self._checker.check_transcript(t)
        assert any("LOAN-FAKE-999999999" in v.description for v in violations)

    def test_correct_loan_id_passes(self) -> None:
        t = _transcript(
            "test-loa-6",
            _ctx(loan_id="LOAN-TEST-001"),
            [_agent_turn(0, "Aapka loan LOAN-TEST-001 hai.")],
        )
        assert self._checker.check_transcript(t) == []

    def test_customer_turns_never_checked(self) -> None:
        # Customer asserting a wrong amount must NOT trigger a LoA violation
        t = _transcript(
            "test-loa-7",
            _ctx(outstanding=10000.0),
            [_customer_turn(0, "Mera bakaya Rs. 99,000 hai.")],
        )
        assert self._checker.check_transcript(t) == []

    def test_no_facts_mentioned_no_violations(self) -> None:
        t = _transcript(
            "test-loa-8",
            _ctx(),
            [_agent_turn(0, "Namaste, main aapki madad karna chahta hun.")],
        )
        assert self._checker.check_transcript(t) == []

    def test_multiple_invented_amounts_multiple_violations(self) -> None:
        # Two invented amounts in one agent turn → 2 violations
        t = _transcript(
            "test-loa-9",
            _ctx(outstanding=10000.0),
            [_agent_turn(0, "Aapka bakaya Rs. 99,000 hai, plus Rs. 88,000 penalty.")],
        )
        violations = self._checker.check_transcript(t)
        assert len(violations) == 2

    def test_negotiation_offer_turn_skipped_by_loa_checker(self) -> None:
        # Agent turn with negotiation_offer_inr set must NOT trigger LoA violation
        # even if the offer amount is not in authoritative set.
        # (NegotiationEnvelopeChecker handles offer bounds separately.)
        t = _transcript(
            "test-loa-neg-skip",
            _ctx(outstanding=20000.0, settlement_pct=0.5),
            [
                # Offer below floor: 1000 not in {20000, 10000} — but must be skipped
                _agent_turn(0, "Hum Rs. 1,000 mein settle kar sakte hain.", offer=1000.0),
            ],
        )
        assert self._checker.check_transcript(t) == []

    def test_violation_contains_authoritative_amounts_in_description(self) -> None:
        t = _transcript(
            "test-loa-10",
            _ctx(outstanding=10000.0, settlement_pct=0.5),
            [_agent_turn(0, "Bakaya Rs. 77,000 hai.")],
        )
        violations = self._checker.check_transcript(t)
        assert len(violations) == 1
        # Description must reference the authoritative values
        assert "10000" in violations[0].description.replace(",", "")


# ---------------------------------------------------------------------------
# RBIComplianceChecker
# ---------------------------------------------------------------------------

class TestRBIComplianceChecker:
    @pytest.fixture(autouse=True)
    def checker(self) -> RBIComplianceChecker:
        self._checker = RBIComplianceChecker()
        return self._checker

    def test_good_call_no_violations(self) -> None:
        t = CallTranscript.from_json(_GOOD_CALL_FIXTURE)
        assert self._checker.check_transcript(t) == []

    def test_calling_hours_violation_fixture(self) -> None:
        t = CallTranscript.from_json(_RBI_HOURS_FIXTURE)
        violations = self._checker.check_transcript(t)
        assert len(violations) == 1
        assert violations[0].checker == "RBICompliance"
        assert "07" in violations[0].description or "calling hours" in violations[0].description.lower()
        assert violations[0].turn_index is None  # call-level violation

    def test_frequency_violation_fixture(self) -> None:
        t = CallTranscript.from_json(_RBI_FREQ_FIXTURE)
        violations = self._checker.check_transcript(t)
        assert len(violations) == 1
        assert violations[0].checker == "RBICompliance"
        assert violations[0].turn_index is None

    def test_hour_8_passes(self) -> None:
        t = _transcript("h8", _ctx(call_hour=8), [])
        assert self._checker.check_transcript(t) == []

    def test_hour_7_fails(self) -> None:
        t = _transcript("h7", _ctx(call_hour=7), [])
        violations = self._checker.check_transcript(t)
        assert len(violations) == 1
        assert "RBI-CALLING-HOURS" in violations[0].description

    def test_hour_19_passes(self) -> None:
        t = _transcript("h19", _ctx(call_hour=19), [])
        assert self._checker.check_transcript(t) == []

    def test_hour_20_fails(self) -> None:
        # 20:00 is the cut-off — 20 is outside the window (exclusive upper bound)
        t = _transcript("h20", _ctx(call_hour=20), [])
        violations = self._checker.check_transcript(t)
        assert len(violations) == 1

    def test_calls_today_2_passes(self) -> None:
        t = _transcript("freq2", _ctx(calls_today=2), [])
        assert self._checker.check_transcript(t) == []

    def test_calls_today_3_fails(self) -> None:
        # 3rd call already made → this call is the 4th → deny
        t = _transcript("freq3", _ctx(calls_today=3), [])
        violations = self._checker.check_transcript(t)
        assert len(violations) == 1
        assert "RBI-MAX-CALLS-PER-DAY" in violations[0].description

    def test_abusive_language_in_agent_turn_caught(self) -> None:
        t = _transcript(
            "abuse-1",
            _ctx(),
            [_agent_turn(0, "Agar nahi diya toh maar dunga saala!")],
        )
        violations = self._checker.check_transcript(t)
        assert any(v.checker == "RBICompliance" and v.turn_index == 0 for v in violations)

    def test_abusive_language_in_customer_turn_ignored(self) -> None:
        # Only agent turns are checked for abusive language
        t = _transcript(
            "abuse-2",
            _ctx(),
            [_customer_turn(0, "Saala agent band karo!")],
        )
        # No hour/frequency violations, customer abusive language is not flagged
        violations = self._checker.check_transcript(t)
        assert len(violations) == 0

    def test_both_violations_together(self) -> None:
        # Out-of-hours AND over-frequency → 2 violations
        t = _transcript("multi", _ctx(call_hour=7, calls_today=4), [])
        violations = self._checker.check_transcript(t)
        assert len(violations) == 2


# ---------------------------------------------------------------------------
# NegotiationEnvelopeChecker
# ---------------------------------------------------------------------------

class TestNegotiationEnvelopeChecker:
    @pytest.fixture(autouse=True)
    def checker(self) -> NegotiationEnvelopeChecker:
        self._checker = NegotiationEnvelopeChecker()
        return self._checker

    def test_good_call_no_violations(self) -> None:
        # good_call fixture: offer = Rs. 25,000, floor = 50% of 50,000 = 25,000
        t = CallTranscript.from_json(_GOOD_CALL_FIXTURE)
        assert self._checker.check_transcript(t) == []

    def test_floor_violation_fixture_one_violation(self) -> None:
        t = CallTranscript.from_json(_NEG_FLOOR_FIXTURE)
        violations = self._checker.check_transcript(t)
        assert len(violations) == 1
        assert violations[0].checker == "NegotiationEnvelope"
        assert "10,000" in violations[0].description
        assert "25,000" in violations[0].description  # the floor

    def test_offer_below_floor_caught(self) -> None:
        # Outstanding 20,000, floor 50% = 10,000; offer 5,000 → violation
        t = _transcript(
            "neg-low",
            _ctx(outstanding=20000.0, settlement_pct=0.5),
            [_agent_turn(0, "Settlement", offer=5000.0)],
        )
        violations = self._checker.check_transcript(t)
        assert len(violations) == 1
        assert violations[0].checker == "NegotiationEnvelope"

    def test_offer_above_ceiling_caught(self) -> None:
        # Outstanding 20,000; offer 25,000 exceeds ceiling
        t = _transcript(
            "neg-high",
            _ctx(outstanding=20000.0),
            [_agent_turn(0, "Settlement", offer=25000.0)],
        )
        violations = self._checker.check_transcript(t)
        assert len(violations) == 1
        assert "ceiling" in violations[0].description.lower() or "25,000" in violations[0].description

    def test_offer_at_exact_floor_passes(self) -> None:
        # Floor = 50% of 20,000 = 10,000; offer = 10,000 → exactly at floor → pass
        t = _transcript(
            "neg-floor-exact",
            _ctx(outstanding=20000.0, settlement_pct=0.5),
            [_agent_turn(0, "Settlement", offer=10000.0)],
        )
        assert self._checker.check_transcript(t) == []

    def test_offer_at_exact_ceiling_passes(self) -> None:
        # Offer equals outstanding → exactly at ceiling → pass
        t = _transcript(
            "neg-ceiling-exact",
            _ctx(outstanding=20000.0),
            [_agent_turn(0, "Full payment", offer=20000.0)],
        )
        assert self._checker.check_transcript(t) == []

    def test_no_offer_no_violation(self) -> None:
        t = _transcript(
            "neg-no-offer",
            _ctx(),
            [_agent_turn(0, "Kya aap payment kar sakte hain?")],  # no offer
        )
        assert self._checker.check_transcript(t) == []

    def test_customer_offer_not_checked(self) -> None:
        # Customer turn with negotiation_offer_inr must not trigger violation
        t = _transcript(
            "neg-cust",
            _ctx(outstanding=20000.0, settlement_pct=0.5),
            [_customer_turn(0, "Main sirf 100 de sakta hun.", offer=100.0)],
        )
        assert self._checker.check_transcript(t) == []

    def test_multiple_offers_multiple_violations(self) -> None:
        # Two agent turns, both below floor
        t = _transcript(
            "neg-multi",
            _ctx(outstanding=20000.0, settlement_pct=0.5),
            [
                _agent_turn(0, "Offer 1", offer=1000.0),
                _agent_turn(2, "Offer 2", offer=2000.0),
            ],
        )
        violations = self._checker.check_transcript(t)
        assert len(violations) == 2


# ---------------------------------------------------------------------------
# IntentAccuracyEvaluator
# ---------------------------------------------------------------------------

@requires_pydantic_v2
class TestIntentAccuracyEvaluator:
    """Tests use the keyword-mode IntentModel (no ONNX file required).

    Texts are chosen so keyword patterns match exactly the expected label
    with no ambiguity from higher-priority rules.

    Requires pydantic>=2.7 (project dependency) — skipped on pydantic v1.
    """

    # Keyword-predictable (text → expected IntentLabel.value)
    _CORRECT_PAIRS: list[tuple[str, str]] = [
        ("Haan", "CONSENT_GRANT"),           # \bhaan\b
        ("yeh galat hai dispute", "DISPUTE"), # \bgalat\b (higher priority wins)
        ("main dunga", "PROMISE_TO_PAY"),     # \bdunga\b
        ("hardship hai", "HARDSHIP"),         # \bhardship\b
        ("baad mein call karo", "CALLBACK"),  # \bbaad mein\b
    ]

    def _make_transcript_with_customer_turns(
        self,
        call_id: str,
        pairs: list[tuple[str, str]],
    ) -> CallTranscript:
        turns = [
            _customer_turn(i * 2 + 1, text, intent)
            for i, (text, intent) in enumerate(pairs)
        ]
        return _transcript(call_id, _ctx(), turns)

    def test_perfect_accuracy_passes(self) -> None:
        evaluator = IntentAccuracyEvaluator()
        t = self._make_transcript_with_customer_turns("ev-1", self._CORRECT_PAIRS)
        result = evaluator.evaluate_transcripts([t])
        assert result.total_labeled == len(self._CORRECT_PAIRS)
        assert result.correct == len(self._CORRECT_PAIRS)
        assert result.accuracy == 1.0
        assert result.passed is True

    def test_below_threshold_fails(self) -> None:
        # All texts produce OTHER, but human labels expect CONSENT_GRANT
        evaluator = IntentAccuracyEvaluator()
        pairs = [("zxywvuts abc", "CONSENT_GRANT")] * 10
        t = self._make_transcript_with_customer_turns("ev-2", pairs)
        result = evaluator.evaluate_transcripts([t])
        assert result.total_labeled == 10
        assert result.correct == 0
        assert result.accuracy == 0.0
        assert result.passed is False

    def test_no_labeled_turns_zero_accuracy(self) -> None:
        evaluator = IntentAccuracyEvaluator()
        # Transcript with only agent turns (no human_intent labels)
        t = _transcript(
            "ev-3",
            _ctx(),
            [_agent_turn(0, "Namaste"), _agent_turn(2, "Shukriya")],
        )
        result = evaluator.evaluate_transcripts([t])
        assert result.total_labeled == 0
        assert result.accuracy == 0.0
        assert result.passed is False

    def test_agent_turns_are_skipped(self) -> None:
        evaluator = IntentAccuracyEvaluator()
        # Agent turn with a human_intent field (unusual but must be ignored)
        turns = [
            TranscriptTurn(
                turn_index=0,
                speaker="agent",
                text="Haan ji",
                human_intent="CONSENT_GRANT",
            ),
            _customer_turn(1, "Haan", "CONSENT_GRANT"),
        ]
        t = _transcript("ev-4", _ctx(), turns)
        result = evaluator.evaluate_transcripts([t])
        # Only the customer turn at index 1 should be evaluated
        assert result.total_labeled == 1

    def test_unlabeled_customer_turns_skipped(self) -> None:
        evaluator = IntentAccuracyEvaluator()
        turns = [
            _customer_turn(0, "Main nahi jaanta", intent=None),  # no label → skip
            _customer_turn(1, "Haan", "CONSENT_GRANT"),
        ]
        t = _transcript("ev-5", _ctx(), turns)
        result = evaluator.evaluate_transcripts([t])
        assert result.total_labeled == 1

    def test_wrong_items_populated_on_mismatch(self) -> None:
        evaluator = IntentAccuracyEvaluator()
        # One correct + one wrong
        pairs = [
            ("Haan", "CONSENT_GRANT"),   # correct
            ("zxywvuts", "CONSENT_GRANT"),  # keyword → OTHER ≠ CONSENT_GRANT → wrong
        ]
        t = self._make_transcript_with_customer_turns("ev-6", pairs)
        result = evaluator.evaluate_transcripts([t])
        assert result.correct == 1
        assert len(result.wrong_items) == 1
        wrong = result.wrong_items[0]
        assert wrong["expected"] == "CONSENT_GRANT"
        assert wrong["predicted"] != "CONSENT_GRANT"
        assert "call_id" in wrong
        assert "text" in wrong

    def test_accuracy_calculation_50_pct(self) -> None:
        evaluator = IntentAccuracyEvaluator()
        correct = [("Haan", "CONSENT_GRANT"), ("main dunga", "PROMISE_TO_PAY")]
        wrong = [("xyzxyz", "CONSENT_GRANT"), ("xyzxyz", "PAYMENT")]
        t = self._make_transcript_with_customer_turns("ev-7", correct + wrong)
        result = evaluator.evaluate_transcripts([t])
        assert result.total_labeled == 4
        assert result.correct == 2
        assert result.accuracy == pytest.approx(0.5, abs=0.001)
        assert result.passed is False

    def test_multiple_transcripts_aggregated(self) -> None:
        evaluator = IntentAccuracyEvaluator()
        t1 = self._make_transcript_with_customer_turns(
            "ev-8a", [("Haan", "CONSENT_GRANT")]
        )
        t2 = self._make_transcript_with_customer_turns(
            "ev-8b", [("main dunga", "PROMISE_TO_PAY")]
        )
        result = evaluator.evaluate_transcripts([t1, t2])
        assert result.total_labeled == 2
        assert result.correct == 2

    def test_custom_model_injection(self) -> None:
        # Inject mock model that always predicts CONSENT_GRANT
        from src.engines.intent.model import IntentModel
        from src.libs.contracts.response_plan import IntentLabel
        scores = [0.0] * IntentModel.NUM_LABELS
        cg_idx = list(IntentLabel).index(IntentLabel.CONSENT_GRANT)
        scores[cg_idx] = 10.0
        mock_model = IntentModel.from_mock(scores)
        evaluator = IntentAccuracyEvaluator(model=mock_model)

        pairs = [("any text", "CONSENT_GRANT"), ("other text", "CONSENT_GRANT")]
        t = self._make_transcript_with_customer_turns("ev-9", pairs)
        result = evaluator.evaluate_transcripts([t])
        assert result.correct == 2
        assert result.accuracy == 1.0


# ---------------------------------------------------------------------------
# FounderValidationSuite
# ---------------------------------------------------------------------------

class TestFounderValidationSuite:
    @pytest.fixture(autouse=True)
    def _stub_evaluator(self) -> None:
        # Use a fixed perfect intent result so tests are deterministic
        self._stub = _FixedIntentEvaluator(
            IntentEvalResult(total_labeled=8, correct=8, accuracy=1.0)
        )

    def _suite(self) -> FounderValidationSuite:
        return FounderValidationSuite(intent_evaluator=self._stub)

    def test_loads_all_five_fixtures(self) -> None:
        suite = self._suite()
        transcripts = suite.load_transcripts(_SYNTHETIC_DIR)
        assert len(transcripts) == 5

    def test_load_transcripts_ignores_underscore_prefix(self, tmp_path: Path) -> None:
        # Copy one fixture and add an _ignored.json
        import shutil
        shutil.copy(_GOOD_CALL_FIXTURE, tmp_path / "good_call_001.json")
        (tmp_path / "_notes.json").write_text("{}", encoding="utf-8")
        suite = self._suite()
        transcripts = suite.load_transcripts(tmp_path)
        assert len(transcripts) == 1

    def test_run_produces_correct_violation_counts(self) -> None:
        report = self._suite().run(_SYNTHETIC_DIR)
        # Expected from fixtures:
        # loa_violation_001: 2 LoA violations
        # rbi_calling_hours: 1 RBI violation
        # rbi_frequency: 1 RBI violation
        # negotiation_floor: 1 neg violation
        assert report.total_loa_violations == 2
        assert report.total_rbi_violations == 2
        assert report.total_negotiation_violations == 1

    def test_run_correct_call_pass_fail_counts(self) -> None:
        report = self._suite().run(_SYNTHETIC_DIR)
        assert report.total_calls == 5
        assert report.calls_passed == 1   # only good_call_001
        assert report.calls_failed == 4

    def test_evaluate_call_good_transcript_passes(self) -> None:
        t = CallTranscript.from_json(_GOOD_CALL_FIXTURE)
        result = self._suite().evaluate_call(t)
        assert result.passed is True
        assert result.total_violations == 0

    def test_evaluate_call_loa_violation_fails(self) -> None:
        t = CallTranscript.from_json(_LOA_VIOLATION_FIXTURE)
        result = self._suite().evaluate_call(t)
        assert result.passed is False
        assert len(result.loa_violations) == 2

    def test_evaluate_call_rbi_hours_violation_fails(self) -> None:
        t = CallTranscript.from_json(_RBI_HOURS_FIXTURE)
        result = self._suite().evaluate_call(t)
        assert result.passed is False
        assert len(result.rbi_violations) == 1

    def test_evaluate_call_neg_floor_violation_fails(self) -> None:
        t = CallTranscript.from_json(_NEG_FLOOR_FIXTURE)
        result = self._suite().evaluate_call(t)
        assert result.passed is False
        assert len(result.negotiation_violations) == 1

    def test_per_call_results_present_for_all_calls(self) -> None:
        report = self._suite().run(_SYNTHETIC_DIR)
        assert len(report.per_call) == 5

    def test_report_contains_injected_intent_eval(self) -> None:
        report = self._suite().run(_SYNTHETIC_DIR)
        assert report.intent_eval is not None
        assert report.intent_eval.total_labeled == 8
        assert report.intent_eval.accuracy == 1.0

    def test_empty_dir_returns_empty_list(self, tmp_path: Path) -> None:
        suite = self._suite()
        transcripts = suite.load_transcripts(tmp_path)
        assert transcripts == []


# ---------------------------------------------------------------------------
# Phase-2 fields must be PENDING (None / False) — never fabricated
# ---------------------------------------------------------------------------

class TestFounderValidationReportPhase2Pending:
    """Verify Sprint-029 Phase-2 dimensions are not populated until real
    infrastructure execution completes.  These assertions are regression guards
    against accidental fabrication of scores."""

    @pytest.fixture
    def report(self) -> FounderValidationReport:
        stub = _FixedIntentEvaluator(
            IntentEvalResult(total_labeled=0, correct=0, accuracy=0.0)
        )
        return FounderValidationSuite(intent_evaluator=stub).run(_SYNTHETIC_DIR)

    def test_tone_empathy_score_none(self, report: FounderValidationReport) -> None:
        assert report.tone_empathy_score is None

    def test_language_naturalness_score_none(self, report: FounderValidationReport) -> None:
        assert report.language_naturalness_score is None

    def test_audio_mos_score_none(self, report: FounderValidationReport) -> None:
        assert report.audio_mos_score is None

    def test_first_audio_p95_none(self, report: FounderValidationReport) -> None:
        assert report.first_audio_p95_ms is None

    def test_call_completion_rate_none(self, report: FounderValidationReport) -> None:
        assert report.call_completion_rate is None

    def test_founder_signed_off_false(self, report: FounderValidationReport) -> None:
        assert report.founder_signed_off is False

    def test_phase1_passed_when_no_violations(self, report: FounderValidationReport) -> None:
        # phase1_passed is True only when all three automated checks have zero violations
        # The synthetic dir has violations, so this should be False
        assert report.phase1_passed is False

    def test_phase1_passed_with_clean_transcript(self) -> None:
        stub = _FixedIntentEvaluator(
            IntentEvalResult(total_labeled=1, correct=1, accuracy=1.0)
        )
        suite = FounderValidationSuite(intent_evaluator=stub)
        # Run against only the good call
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            shutil.copy(_GOOD_CALL_FIXTURE, tmp_path / "good_call_001.json")
            report = suite.run(tmp_path)
        assert report.phase1_passed is True
        assert report.calls_passed == 1
        assert report.calls_failed == 0

    def test_phase2_fields_not_set_by_run(self) -> None:
        # Ensure run() never touches phase-2 fields
        stub = _FixedIntentEvaluator(
            IntentEvalResult(total_labeled=0, correct=0, accuracy=0.0)
        )
        report = FounderValidationSuite(intent_evaluator=stub).run(_SYNTHETIC_DIR)
        phase2_fields = [
            report.tone_empathy_score,
            report.language_naturalness_score,
            report.audio_mos_score,
            report.first_audio_p95_ms,
            report.call_completion_rate,
        ]
        assert all(f is None for f in phase2_fields), (
            f"Phase-2 fields must be None — found: {phase2_fields}"
        )
        assert report.founder_signed_off is False


# ---------------------------------------------------------------------------
# CallEvalResult properties
# ---------------------------------------------------------------------------

class TestCallEvalResult:
    def test_total_violations_sum(self) -> None:
        loa = [Violation("LawOfAuthority", "c1", 0, "desc")]
        rbi = [Violation("RBICompliance", "c1", None, "desc"), Violation("RBICompliance", "c1", None, "d2")]
        result = CallEvalResult(call_id="c1", loa_violations=loa, rbi_violations=rbi, negotiation_violations=[])
        assert result.total_violations == 3

    def test_passed_when_no_violations(self) -> None:
        result = CallEvalResult(call_id="c2", loa_violations=[], rbi_violations=[], negotiation_violations=[])
        assert result.passed is True

    def test_failed_when_any_violation(self) -> None:
        result = CallEvalResult(
            call_id="c3",
            loa_violations=[Violation("LawOfAuthority", "c3", 0, "d")],
            rbi_violations=[],
            negotiation_violations=[],
        )
        assert result.passed is False


# ---------------------------------------------------------------------------
# FounderValidationReport properties
# ---------------------------------------------------------------------------

class TestFounderValidationReportProperties:
    def _make_report(
        self,
        loa: int = 0,
        rbi: int = 0,
        neg: int = 0,
        intent: IntentEvalResult | None = None,
    ) -> FounderValidationReport:
        return FounderValidationReport(
            total_calls=1,
            calls_passed=1 if loa + rbi + neg == 0 else 0,
            calls_failed=0 if loa + rbi + neg == 0 else 1,
            total_loa_violations=loa,
            total_rbi_violations=rbi,
            total_negotiation_violations=neg,
            intent_eval=intent,
            per_call=[],
        )

    def test_loa_passed_property(self) -> None:
        assert self._make_report(loa=0).loa_passed is True
        assert self._make_report(loa=1).loa_passed is False

    def test_rbi_passed_property(self) -> None:
        assert self._make_report(rbi=0).rbi_passed is True
        assert self._make_report(rbi=1).rbi_passed is False

    def test_negotiation_passed_property(self) -> None:
        assert self._make_report(neg=0).negotiation_passed is True
        assert self._make_report(neg=1).negotiation_passed is False

    def test_intent_passed_none(self) -> None:
        assert self._make_report(intent=None).intent_passed is False

    def test_intent_passed_above_threshold(self) -> None:
        ie = IntentEvalResult(total_labeled=10, correct=10, accuracy=1.0)
        assert self._make_report(intent=ie).intent_passed is True

    def test_intent_passed_below_threshold(self) -> None:
        ie = IntentEvalResult(total_labeled=10, correct=8, accuracy=0.8)
        assert self._make_report(intent=ie).intent_passed is False

    def test_phase1_passed_all_zero(self) -> None:
        assert self._make_report().phase1_passed is True

    def test_phase1_failed_on_any_violation(self) -> None:
        assert self._make_report(loa=1).phase1_passed is False
        assert self._make_report(rbi=1).phase1_passed is False
        assert self._make_report(neg=1).phase1_passed is False
