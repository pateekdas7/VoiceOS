"""10-turn live validation of the Sales Intelligence pipeline.

Simulates 10 turns through the actual CIL pipeline (real engine instances,
no mocks for the sales layer). Runs WITHOUT needing Twilio/STT/TTS.

Usage:
    python -m pytest tests/integration/sales/test_10turn_sales_pipeline.py -v -s

Or run standalone:
    python tests/integration/sales/test_10turn_sales_pipeline.py

Architecture: VoiceOS Phase 2 Sales Intelligence Layer.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path when run standalone
sys.path.insert(0, str(Path(__file__).parents[3]))

from src.engines.entity_extraction.result import ExtractedEntities
from src.engines.goal_planner.goals import Goal
from src.engines.intent.result import IntentResult
from src.engines.risk.flags import RiskFlag
from src.engines.risk.result import RiskAssessment
from src.engines.sales.action_planner import SalesActionPlanner
from src.engines.sales.domains.real_estate import RealEstateDomainConfig
from src.engines.sales.question_selector import QuestionSelector
from src.engines.sales.schema import SalesAction, SalesState
from src.engines.sales.state_updater import SalesStateUpdater
from src.engines.strategy.actions import StrategyAction
from src.engines.strategy.engine import StrategySelection
from src.libs.contracts.response_plan import IntentLabel


# ---------------------------------------------------------------------------
# Engine construction
# ---------------------------------------------------------------------------

DOMAIN = RealEstateDomainConfig()
UPDATER = SalesStateUpdater(domain=DOMAIN)
SELECTOR = QuestionSelector(domain=DOMAIN)
PLANNER = SalesActionPlanner(domain=DOMAIN)


# ---------------------------------------------------------------------------
# Conversation specification (10 turns from the spec)
# ---------------------------------------------------------------------------

CONVERSATION = [
    # (utterance, intent_label, risk_flags, strategy_action)
    (
        "Main Noida Extension mein 3 BHK dekh raha hoon.",
        IntentLabel.OTHER,
        [],
        StrategyAction.ASK,
    ),
    (
        "Budget around 80 lakh hai.",
        IntentLabel.OTHER,
        [],
        StrategyAction.ASK,
    ),
    (
        "Investment ke liye chahiye.",
        IntentLabel.OTHER,
        [],
        StrategyAction.ASK,
    ),
    (
        "Price bahut high lag raha hai.",
        IntentLabel.DISPUTE,
        [RiskFlag.DISPUTE_CLAIM],
        StrategyAction.VERIFY,
    ),
    (
        "Theek hai, 85 lakh tak stretch kar sakta hoon.",
        IntentLabel.OTHER,
        [],
        StrategyAction.ASK,
    ),
    (
        "Agle 2 mahine mein chahiye.",
        IntentLabel.OTHER,
        [],
        StrategyAction.ASK,
    ),
    (
        "Main akela decide kar sakta hoon.",
        IntentLabel.OTHER,
        [],
        StrategyAction.ASK,
    ),
    (
        "Home loan se lena hai.",
        IntentLabel.OTHER,
        [],
        StrategyAction.ASK,
    ),
    (
        "Kya site visit ho sakta hai?",
        IntentLabel.OTHER,
        [],
        StrategyAction.ASK,
    ),
    (
        "Mujhe kisi senior se baat karni hai.",
        IntentLabel.OTHER,
        [RiskFlag.ESCALATION_TRIGGER],
        StrategyAction.ESCALATE,
    ),
]


def run_pipeline(verbose: bool = True) -> list[dict]:
    """Run the 10-turn pipeline and return structured turn reports."""
    previous_state: SalesState | None = None
    reports: list[dict] = []
    asked_fields: list[str] = []

    for turn_num, (utterance, intent_label, risk_flags, strategy_action) in enumerate(CONVERSATION, 1):
        intent = IntentResult(
            label=intent_label,
            confidence=0.9,
            reasoning_hint=f"turn {turn_num}",
            source_span=utterance,
        )
        risk = RiskAssessment(
            flags=risk_flags,
            escalation_required=bool(risk_flags and RiskFlag.ESCALATION_TRIGGER in risk_flags),
            human_handoff_required=bool(risk_flags and RiskFlag.ABUSE_DETECTED in risk_flags),
        )
        strategy = StrategySelection(
            action=strategy_action,
            confidence=0.9,
            rationale=f"turn {turn_num} test strategy",
        )

        state = UPDATER.update(
            previous_state=previous_state,
            entities=ExtractedEntities(slots={}, confidence=0.0),
            intent=intent,
            conversation_state="DEBT_DISCUSSION",
            strategy=strategy,
            risk=risk,
        )
        next_q = SELECTOR.select(state, intent, strategy, risk)
        action = PLANNER.plan(state, intent, strategy, risk, Goal.SECURE_PTP, next_q)

        state.next_action = action
        state.next_question = next_q

        # Track asked fields
        if next_q and next_q.value not in asked_fields:
            asked_fields.append(next_q.value)

        report = {
            "turn": turn_num,
            "customer": utterance,
            "intent": f"{intent.label.value} ({intent.confidence:.2f})",
            "risk_flags": [f.value for f in risk_flags],
            "strategy": strategy_action.value,
            "lead_stage": state.lead_stage.value,
            "lead_temperature": state.lead_temperature.value,
            "qualification_score": state.qualification_score,
            "confirmed_fields": state.confirmed_fields[:],
            "uncertain_fields": state.uncertain_fields[:],
            "objections": state.objections[:],
            "budget_min": state.budget_min,
            "budget_max": state.budget_max,
            "next_question": next_q.value if next_q else None,
            "next_action": action.value,
        }

        if verbose:
            print(f"\n{'='*60}")
            print(f"TURN {turn_num}")
            print(f"Customer: {utterance}")
            print(f"Intent: {report['intent']}")
            print(f"Risk flags: {report['risk_flags']}")
            print(f"Strategy: {report['strategy']}")
            print(f"Lead: stage={report['lead_stage']} temp={report['lead_temperature']}")
            print(f"Budget: ₹{(state.budget_min or 0)//100000}L – ₹{(state.budget_max or 0)//100000}L")
            print(f"Confirmed fields: {report['confirmed_fields']}")
            print(f"Uncertain fields: {report['uncertain_fields']}")
            print(f"Objections: {report['objections']}")
            print(f"Qualification score: {report['qualification_score']}")
            print(f"Next Question: {report['next_question']}")
            print(f"Next Action: {report['next_action']}")

        reports.append(report)
        previous_state = state

    if verbose:
        print(f"\n{'='*60}")
        print(f"ASKED FIELDS (ordered): {asked_fields}")
        print(f"No duplicates: {len(asked_fields) == len(set(asked_fields))}")

    return reports


# ---------------------------------------------------------------------------
# pytest tests using the pipeline output
# ---------------------------------------------------------------------------

import pytest  # noqa: E402 — after sys.path setup


@pytest.fixture(scope="module")
def pipeline_reports():
    return run_pipeline(verbose=False)


def test_no_confirmed_field_re_asked(pipeline_reports):
    """Once a field is confirmed, it is never asked again in subsequent turns."""
    previous_confirmed: set[str] = set()
    for i, report in enumerate(pipeline_reports):
        q = report["next_question"]
        if q is not None and q in previous_confirmed:
            raise AssertionError(
                f"Turn {i+1}: field {q} was asked again after being confirmed. "
                f"Previously confirmed: {previous_confirmed}"
            )
        # Update confirmed set at end of this turn
        previous_confirmed = set(report["confirmed_fields"])


def test_turn1_location_extracted(pipeline_reports):
    r = pipeline_reports[0]
    assert "LOCATION" in r["confirmed_fields"]


def test_turn2_budget_80_lakh(pipeline_reports):
    r = pipeline_reports[1]
    assert r["budget_max"] == 8_000_000, f"Expected 8000000, got {r['budget_max']}"


def test_turn4_objection_handled(pipeline_reports):
    r = pipeline_reports[3]
    assert r["next_action"] == SalesAction.HANDLE_OBJECTION.value, (
        f"Turn 4 should be HANDLE_OBJECTION, got {r['next_action']}"
    )


def test_turn5_budget_updated_to_85l(pipeline_reports):
    r = pipeline_reports[4]
    assert r["budget_max"] == 8_500_000, f"Expected 8500000, got {r['budget_max']}"


def test_turn10_human_handoff(pipeline_reports):
    r = pipeline_reports[9]
    assert r["next_action"] == SalesAction.HUMAN_HANDOFF.value, (
        f"Turn 10 should be HUMAN_HANDOFF, got {r['next_action']}"
    )


def test_qualification_score_increases(pipeline_reports):
    score_t1 = pipeline_reports[0]["qualification_score"]
    score_t6 = pipeline_reports[5]["qualification_score"]
    assert score_t6 > score_t1, f"Score should increase: t1={score_t1} t6={score_t6}"


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    reports = run_pipeline(verbose=True)
    print("\n\n=== PIPELINE COMPLETE ===")
    print(f"Total turns: {len(reports)}")
    final = reports[-1]
    print(f"Final action: {final['next_action']}")
    print(f"Final score: {final['qualification_score']}")
    print(f"Final confirmed: {final['confirmed_fields']}")
