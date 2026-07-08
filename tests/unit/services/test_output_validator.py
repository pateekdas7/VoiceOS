"""Unit tests for OutputValidator — RI-5, RI-6, must_not_say, must_say."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from src.libs.contracts.response_plan import (
    MustNotSayItem,
    ResponsePlan,
    StrategyAction,
    StrategyLabel,
)
from src.services.llm_runtime.output_validator import OutputValidator


def _make_plan(**kwargs: Any) -> ResponsePlan:
    defaults: dict[str, object] = {
        "plan_id": str(uuid.uuid4()),
        "version": 1,
        "call_id": "call-001",
        "tenant_id": "tenant-001",
        "created_at": datetime.utcnow(),
        "strategy": StrategyAction(action=StrategyLabel.ASK),
    }
    defaults.update(kwargs)
    return ResponsePlan(**defaults)  # type: ignore[arg-type]


class TestOutputValidator:
    def test_output_validator_accepts_grounded_output(self) -> None:  # required named test
        """Valid output with no violations should be accepted."""
        validator = OutputValidator()
        plan = _make_plan(facts={"outstanding_balance_minor": 50_000})
        result = validator.validate("Aapka outstanding balance ₹500 hai.", plan)
        assert result.valid
        assert result.violations == []

    def test_output_validator_rejects_hallucination(self) -> None:  # required named test
        """Amount deviating >25% from plan.facts should be rejected (RI-5)."""
        validator = OutputValidator()
        plan = _make_plan(facts={"outstanding_balance_minor": 50_000})  # ₹500
        # Output claims ₹5000 — 10x deviation.
        result = validator.validate("Aapka outstanding ₹5000 hai.", plan)
        assert not result.valid
        assert any("RI-5" in v for v in result.violations)

    def test_output_validator_rejects_must_not_say(self) -> None:  # required named test
        """Output matching must_not_say pattern must be rejected."""
        validator = OutputValidator()
        plan = _make_plan(
            must_not_say=(
                MustNotSayItem(
                    item_id="NO_THREATS",
                    description="Threats of legal action",
                    pattern=r"\b(court|jail|police)\b",
                ),
            )
        )
        result = validator.validate("We will take you to court for non-payment.", plan)
        assert not result.valid
        assert any("Must-not-say" in v for v in result.violations)

    def test_empty_output_rejected(self) -> None:
        validator = OutputValidator()
        plan = _make_plan()
        result = validator.validate("", plan)
        assert not result.valid
        assert "empty" in result.violations[0].lower()

    def test_fallback_response_provided_on_rejection(self) -> None:
        validator = OutputValidator()
        plan = _make_plan(must_not_say=(MustNotSayItem(item_id="X", description="ban", pattern=r"\bban\b"),))
        result = validator.validate("This is ban.", plan)
        assert not result.valid
        assert result.fallback_response != ""

    def test_plan_id_stored_in_result(self) -> None:
        validator = OutputValidator()
        plan = _make_plan()
        result = validator.validate("Theek hai, aaj karte hain.", plan)
        assert result.plan_id == plan.plan_id

    def test_short_output_rejected(self) -> None:
        validator = OutputValidator()
        plan = _make_plan()
        result = validator.validate("ok", plan)
        assert not result.valid

    def test_amount_within_tolerance_accepted(self) -> None:
        validator = OutputValidator()
        plan = _make_plan(facts={"outstanding_balance_minor": 100_000})  # ₹1000
        # ₹1050 — 5% deviation, within 25% tolerance.
        result = validator.validate("Aapka balance ₹1050 hai.", plan)
        assert result.valid
