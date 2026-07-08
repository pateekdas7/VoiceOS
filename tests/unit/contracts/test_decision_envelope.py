"""Unit tests for src/libs/contracts/decision.py.

Tests: DecisionEnvelope creation, GovernanceVerdict enum values,
       DecisionRecord construction, JSON serialisation round-trip.

AC-1: decision module types pass mypy --strict.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from pydantic import ValidationError

from src.libs.contracts.decision import (
    DecisionEnvelope,
    DecisionReason,
    DecisionRecord,
    GovernanceStatus,
    GovernanceVerdict,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_reason(decision: str = "INTENT=PAYMENT") -> DecisionReason:
    return DecisionReason(
        decision_id="reason-001",
        source_engine="IntentEngine",
        decision=decision,
        confidence=0.92,
    )


def make_record(call_id: str = "call-abc") -> DecisionRecord:
    return DecisionRecord(
        record_id="rec-001",
        call_id=call_id,
        tenant_id="tenant-xyz",
        reason=make_reason(),
    )


def make_envelope(**overrides: object) -> DecisionEnvelope:
    defaults: dict[str, object] = {
        "envelope_id": "env-001",
        "call_id": "call-abc",
        "tenant_id": "tenant-xyz",
        "timestamp": datetime(2026, 6, 30, 10, 0, 0),
        "response_plan_id": "plan-001",
        "correlation_id": "corr-001",
        "trace_id": "trace-001",
    }
    defaults.update(overrides)
    return DecisionEnvelope(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# GovernanceVerdict and GovernanceStatus
# ---------------------------------------------------------------------------


class TestGovernanceVerdict:
    def test_approve_status(self) -> None:
        v = GovernanceVerdict(status=GovernanceStatus.APPROVE)
        assert v.status == GovernanceStatus.APPROVE
        assert v.violations == ()

    def test_require_human_status(self) -> None:
        v = GovernanceVerdict(status=GovernanceStatus.REQUIRE_HUMAN, explanation="Borderline")
        assert v.status == GovernanceStatus.REQUIRE_HUMAN
        assert "Borderline" in v.explanation

    def test_block_status(self) -> None:
        v = GovernanceVerdict(
            status=GovernanceStatus.BLOCK,
            violations=("LAW_OF_AUTHORITY_RI5",),
            explanation="Hallucinated amount",
        )
        assert v.status == GovernanceStatus.BLOCK
        assert "LAW_OF_AUTHORITY_RI5" in v.violations

    def test_all_governance_statuses_exist(self) -> None:
        statuses = {s.value for s in GovernanceStatus}
        assert "APPROVE" in statuses
        assert "REQUIRE_HUMAN" in statuses
        assert "BLOCK" in statuses

    def test_verdict_is_immutable(self) -> None:
        v = GovernanceVerdict(status=GovernanceStatus.APPROVE)
        with pytest.raises(ValidationError):
            v.status = GovernanceStatus.BLOCK  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DecisionReason
# ---------------------------------------------------------------------------


class TestDecisionReason:
    def test_creation(self) -> None:
        r = make_reason("STRATEGY=NEGOTIATE")
        assert r.source_engine == "IntentEngine"
        assert r.confidence == pytest.approx(0.92)

    def test_confidence_bounds(self) -> None:
        with pytest.raises(ValidationError):
            DecisionReason(decision_id="r", source_engine="E", decision="D", confidence=1.5)

    def test_evidence_tuple(self) -> None:
        r = DecisionReason(
            decision_id="r",
            source_engine="E",
            decision="D",
            confidence=0.8,
            evidence=("span1", "span2"),
        )
        assert len(r.evidence) == 2
        assert r.evidence[0] == "span1"

    def test_immutable(self) -> None:
        r = make_reason()
        with pytest.raises(ValidationError):
            r.decision = "different"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DecisionEnvelope
# ---------------------------------------------------------------------------


class TestDecisionEnvelope:
    def test_minimal_envelope(self) -> None:
        env = make_envelope()
        assert env.envelope_id == "env-001"
        assert env.call_id == "call-abc"
        assert env.tenant_id == "tenant-xyz"
        assert env.response_plan_id == "plan-001"

    def test_default_governance_verdict(self) -> None:
        env = make_envelope()
        assert env.governance_verdict.status == GovernanceStatus.APPROVE

    def test_with_decisions(self) -> None:
        records = (make_record(),)
        env = make_envelope(decisions=records)
        assert len(env.decisions) == 1
        assert env.decisions[0].record_id == "rec-001"

    def test_with_custom_governance_verdict(self) -> None:
        verdict = GovernanceVerdict(
            status=GovernanceStatus.BLOCK,
            violations=("RI5_VIOLATION",),
        )
        env = make_envelope(governance_verdict=verdict)
        assert env.governance_verdict.status == GovernanceStatus.BLOCK

    def test_causation_id_optional(self) -> None:
        env = make_envelope(causation_id=None)
        assert env.causation_id is None

        env2 = make_envelope(causation_id="event-001")
        assert env2.causation_id == "event-001"

    def test_immutable(self) -> None:
        env = make_envelope()
        with pytest.raises(ValidationError):
            env.envelope_id = "tampered"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# JSON serialisation round-trip
# ---------------------------------------------------------------------------


class TestDecisionEnvelopeSerialization:
    def test_json_round_trip(self) -> None:
        env = make_envelope()
        j = env.model_dump_json()
        env2 = DecisionEnvelope.model_validate_json(j)
        assert env2.envelope_id == env.envelope_id
        assert env2.response_plan_id == env.response_plan_id

    def test_json_contains_expected_keys(self) -> None:
        env = make_envelope()
        data = json.loads(env.model_dump_json())
        assert "envelope_id" in data
        assert "call_id" in data
        assert "tenant_id" in data
        assert "timestamp" in data
        assert "governance_verdict" in data
        assert "decisions" in data

    def test_json_round_trip_with_decisions(self) -> None:
        records = (make_record("call-test"),)
        env = make_envelope(decisions=records)
        env2 = DecisionEnvelope.model_validate_json(env.model_dump_json())
        assert len(env2.decisions) == 1
        assert env2.decisions[0].call_id == "call-test"
