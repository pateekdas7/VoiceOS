"""Phase 3 Production Action Layer — unit tests.

Covers:
- PipelineTransitionEngine FSM validation
- SalesProductionActionDispatcher (SCHEDULE_FOLLOWUP, HUMAN_HANDOFF, dedup)
- generate_post_call_summary serialization
- RelationshipContextBuilder prompt block
- PostCallSummaryRepository save/get + tenant isolation
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# cffi/_cffi_backend is a compiled C extension required by psycopg2.
# Not available on Termux — skip repository tests that depend on it.
_CFFI_AVAILABLE = importlib.util.find_spec("_cffi_backend") is not None

from src.engines.sales.pipeline_transitions import (
    InvalidTransitionError,
    PipelineTransition,
    PipelineTransitionEngine,
)
from src.engines.sales.post_call_summary import generate_post_call_summary
from src.engines.sales.production_actions import SalesProductionActionDispatcher
from src.engines.sales.relationship_context import RelationshipContextBuilder
from src.engines.sales.schema import (
    DecisionMaker,
    FinancingStatus,
    LeadIntent,
    LeadStage,
    LeadTemperature,
    PropertyPurpose,
    QualificationStatus,
    SalesAction,
    SalesObjective,
    SalesState,
    SiteVisitInterest,
    Timeline,
)


# ---------------------------------------------------------------------------
# PipelineTransitionEngine
# ---------------------------------------------------------------------------


class TestPipelineTransitionEngine:
    def test_valid_forward_transition_returns_record(self):
        result = PipelineTransitionEngine.evaluate(
            LeadStage.NEW, LeadStage.ENGAGED, reason="greeted", trigger="SalesStateUpdater"
        )
        assert result is not None
        assert isinstance(result, PipelineTransition)
        assert result.from_stage == LeadStage.NEW
        assert result.to_stage == LeadStage.ENGAGED

    def test_same_stage_returns_none(self):
        result = PipelineTransitionEngine.evaluate(
            LeadStage.ENGAGED, LeadStage.ENGAGED, reason="no change"
        )
        assert result is None

    def test_terminal_converted_raises(self):
        with pytest.raises(InvalidTransitionError) as exc_info:
            PipelineTransitionEngine.evaluate(
                LeadStage.CONVERTED, LeadStage.ENGAGED, reason="illegal"
            )
        assert "CONVERTED" in str(exc_info.value)
        assert "ENGAGED" in str(exc_info.value)

    def test_terminal_disqualified_raises(self):
        with pytest.raises(InvalidTransitionError):
            PipelineTransitionEngine.evaluate(
                LeadStage.DISQUALIFIED, LeadStage.NEW, reason="illegal"
            )

    def test_backward_transition_qualified_to_new_raises(self):
        with pytest.raises(InvalidTransitionError):
            PipelineTransitionEngine.evaluate(
                LeadStage.QUALIFIED, LeadStage.NEW, reason="illegal backward"
            )

    def test_nurturing_to_engaged_allowed(self):
        result = PipelineTransitionEngine.evaluate(
            LeadStage.NURTURING, LeadStage.ENGAGED, reason="re-engaged"
        )
        assert result is not None
        assert result.to_stage == LeadStage.ENGAGED

    def test_nurturing_to_qualifying_allowed(self):
        result = PipelineTransitionEngine.evaluate(
            LeadStage.NURTURING, LeadStage.QUALIFYING, reason="warmed up"
        )
        assert result is not None

    def test_site_visit_to_converted_allowed(self):
        result = PipelineTransitionEngine.evaluate(
            LeadStage.SITE_VISIT_SCHEDULED, LeadStage.CONVERTED, reason="deal closed"
        )
        assert result is not None

    def test_negotiating_to_disqualified_allowed(self):
        result = PipelineTransitionEngine.evaluate(
            LeadStage.NEGOTIATING, LeadStage.DISQUALIFIED, reason="budget mismatch"
        )
        assert result is not None

    def test_error_carries_from_and_to_stages(self):
        with pytest.raises(InvalidTransitionError) as exc_info:
            PipelineTransitionEngine.evaluate(
                LeadStage.NEW, LeadStage.CONVERTED, reason="skip"
            )
        err = exc_info.value
        assert err.from_stage == LeadStage.NEW
        assert err.to_stage == LeadStage.CONVERTED

    def test_default_trigger_is_salesstateupdater(self):
        result = PipelineTransitionEngine.evaluate(
            LeadStage.NEW, LeadStage.ENGAGED, reason="greeted"
        )
        assert result is not None
        assert result.trigger == "SalesStateUpdater"


# ---------------------------------------------------------------------------
# SalesProductionActionDispatcher
# ---------------------------------------------------------------------------


class TestSalesProductionActionDispatcher:
    def _make_state(self, action: str = "SCHEDULE_FOLLOWUP", callback_time: str | None = None) -> dict:
        s = SalesState()
        s.next_action = SalesAction(action)
        if callback_time:
            s.requested_callback_time = datetime.fromisoformat(callback_time)
        return s.to_dict()

    def test_schedule_followup_calls_scheduler(self):
        mock_scheduler = MagicMock()
        mock_scheduler.schedule.return_value = MagicMock()

        dispatcher = SalesProductionActionDispatcher(callback_scheduler=mock_scheduler)
        state = self._make_state("SCHEDULE_FOLLOWUP", "2026-10-01T10:00:00")

        result = dispatcher.dispatch(state, context=None, tenant_id="t1", call_id="c1")

        assert result == SalesAction.SCHEDULE_FOLLOWUP
        mock_scheduler.schedule.assert_called_once()
        call_kwargs = mock_scheduler.schedule.call_args
        assert call_kwargs.kwargs.get("timezone") == "Asia/Kolkata"

    def test_schedule_followup_dedup_skips_second_call(self):
        mock_scheduler = MagicMock()
        mock_scheduler.schedule.return_value = MagicMock()

        dispatcher = SalesProductionActionDispatcher(callback_scheduler=mock_scheduler)
        state = self._make_state("SCHEDULE_FOLLOWUP", "2026-10-01T10:00:00")

        dispatcher.dispatch(state, context=None, tenant_id="t1", call_id="c1")
        result = dispatcher.dispatch(state, context=None, tenant_id="t1", call_id="c1")

        assert result == SalesAction.SCHEDULE_FOLLOWUP
        assert mock_scheduler.schedule.call_count == 1  # deduped

    def test_schedule_followup_no_callback_time_skips(self):
        mock_scheduler = MagicMock()
        dispatcher = SalesProductionActionDispatcher(callback_scheduler=mock_scheduler)
        state = self._make_state("SCHEDULE_FOLLOWUP", None)

        result = dispatcher.dispatch(state, context=None, tenant_id="t1", call_id="c1")

        assert result is None
        mock_scheduler.schedule.assert_not_called()

    def test_human_handoff_returns_signal(self):
        dispatcher = SalesProductionActionDispatcher(callback_scheduler=None)
        state = self._make_state("HUMAN_HANDOFF")

        result = dispatcher.dispatch(state, context=None, tenant_id="t1", call_id="c1")

        assert result == SalesAction.HUMAN_HANDOFF

    def test_no_action_returns_none(self):
        dispatcher = SalesProductionActionDispatcher(callback_scheduler=None)
        state = self._make_state("DISCOVER")

        result = dispatcher.dispatch(state, context=None, tenant_id="t1", call_id="c1")

        assert result is None

    def test_empty_state_returns_none(self):
        dispatcher = SalesProductionActionDispatcher(callback_scheduler=None)
        assert dispatcher.dispatch(None, context=None, tenant_id="t1", call_id="c1") is None

    def test_no_scheduler_wired_skips_followup(self):
        dispatcher = SalesProductionActionDispatcher(callback_scheduler=None)
        state = self._make_state("SCHEDULE_FOLLOWUP", "2026-10-01T10:00:00")
        result = dispatcher.dispatch(state, context=None, tenant_id="t1", call_id="c1")
        assert result is None

    def test_different_call_ids_both_scheduled(self):
        mock_scheduler = MagicMock()
        mock_scheduler.schedule.return_value = MagicMock()

        dispatcher = SalesProductionActionDispatcher(callback_scheduler=mock_scheduler)
        state = self._make_state("SCHEDULE_FOLLOWUP", "2026-10-01T10:00:00")

        dispatcher.dispatch(state, context=None, tenant_id="t1", call_id="call-A")
        dispatcher.dispatch(state, context=None, tenant_id="t1", call_id="call-B")

        assert mock_scheduler.schedule.call_count == 2


# ---------------------------------------------------------------------------
# generate_post_call_summary
# ---------------------------------------------------------------------------


class TestGeneratePostCallSummary:
    def _full_state(self) -> SalesState:
        s = SalesState()
        s.lead_stage = LeadStage.QUALIFIED
        s.lead_intent = LeadIntent.HIGH
        s.lead_temperature = LeadTemperature.HOT
        s.qualification_status = QualificationStatus.FULLY_QUALIFIED
        s.qualification_score = 85
        s.budget_min = 5_000_000
        s.budget_max = 8_000_000
        s.location = ["Whitefield", "Koramangala"]
        s.property_type = ["3BHK"]
        s.purpose = PropertyPurpose.SELF_USE
        s.timeline = Timeline.MONTHS_3_6
        s.decision_maker = DecisionMaker.SELF
        s.financing_status = FinancingStatus.HOME_LOAN
        s.site_visit_interest = SiteVisitInterest.INTERESTED
        s.current_objective = SalesObjective.QUALIFY
        s.next_action = SalesAction.SCHEDULE_FOLLOWUP  # triggers callback_requested=True
        s.last_sales_action = SalesAction.OFFER_SITE_VISIT
        s.objections = ["price_high"]
        s.confirmed_fields = ["budget", "location"]
        s.requested_callback_time = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
        return s

    def test_full_state_serializes_correctly(self):
        summary = generate_post_call_summary(self._full_state(), call_id="c1", customer_id="cust1")
        assert summary["call_id"] == "c1"
        assert summary["customer_id"] == "cust1"
        assert summary["lead"]["stage"] == "QUALIFIED"
        assert summary["lead"]["qualification_score"] == 85
        assert summary["requirements"]["budget"]["min_rupees"] == 5_000_000
        assert "Whitefield" in summary["requirements"]["locations"]
        assert summary["requirements"]["purpose"] == "SELF_USE"
        assert summary["conversation"]["escalated"] is False
        assert summary["conversation"]["callback_requested"] is True
        assert "2026-10-01" in summary["conversation"]["requested_callback_time"]
        assert summary["outcome"] == "qualified"

    def test_escalated_flag_propagates(self):
        summary = generate_post_call_summary(
            self._full_state(), call_id="c1", customer_id="cust1", escalated=True
        )
        assert summary["conversation"]["escalated"] is True

    def test_new_lead_minimal_state(self):
        s = SalesState()
        summary = generate_post_call_summary(s, call_id="c2", customer_id="cust2")
        assert summary["lead"]["stage"] == "NEW"
        assert summary["requirements"]["budget"] is None
        assert summary["outcome"] == "no_engagement"

    def test_converted_outcome(self):
        s = SalesState()
        s.lead_stage = LeadStage.CONVERTED
        summary = generate_post_call_summary(s)
        assert summary["outcome"] == "converted"

    def test_no_values_invented(self):
        s = SalesState()
        summary = generate_post_call_summary(s)
        assert summary["requirements"]["purpose"] is None
        assert summary["requirements"]["financing_status"] is None
        assert summary["conversation"]["requested_callback_time"] is None


# ---------------------------------------------------------------------------
# RelationshipContextBuilder
# ---------------------------------------------------------------------------


class TestRelationshipContextBuilder:
    def _make_rm(self, **kwargs) -> MagicMock:
        rm = MagicMock()
        rm.total_calls = kwargs.get("total_calls", 0)
        rm.last_call_outcome = kwargs.get("last_call_outcome", None)
        rm.preferred_language = kwargs.get("preferred_language", "hi-IN")
        rm.escalation_count = kwargs.get("escalation_count", 0)
        rm.sentiment_history = kwargs.get("sentiment_history", [])
        return rm

    def test_none_returns_empty(self):
        assert RelationshipContextBuilder.build_block(None) == ""

    def test_no_history_returns_empty(self):
        rm = self._make_rm()
        assert RelationshipContextBuilder.build_block(rm) == ""

    def test_past_calls_and_outcome_included(self):
        rm = self._make_rm(total_calls=3, last_call_outcome="qualified")
        block = RelationshipContextBuilder.build_block(rm)
        assert "HISTORICAL" in block
        assert "3" in block
        assert "qualified" in block

    def test_non_default_language_included(self):
        rm = self._make_rm(total_calls=1, preferred_language="en-US")
        block = RelationshipContextBuilder.build_block(rm)
        assert "en-US" in block

    def test_default_language_not_included(self):
        rm = self._make_rm(total_calls=1, preferred_language="hi-IN")
        block = RelationshipContextBuilder.build_block(rm)
        assert "hi-IN" not in block

    def test_escalation_count_included(self):
        rm = self._make_rm(total_calls=2, escalation_count=1)
        block = RelationshipContextBuilder.build_block(rm)
        assert "escalation" in block.lower()

    def test_zero_escalation_not_included(self):
        rm = self._make_rm(total_calls=1, escalation_count=0)
        block = RelationshipContextBuilder.build_block(rm)
        assert "escalation" not in block.lower()

    def test_sentiment_history_included(self):
        rm = self._make_rm(total_calls=1, sentiment_history=["positive", "neutral", "positive"])
        block = RelationshipContextBuilder.build_block(rm)
        assert "positive" in block

    def test_block_labeled_historical(self):
        rm = self._make_rm(total_calls=2)
        block = RelationshipContextBuilder.build_block(rm)
        assert "HISTORICAL CUSTOMER CONTEXT" in block
        assert "do NOT treat as current state" in block

    def test_max_lines_respected(self):
        rm = self._make_rm(
            total_calls=5,
            last_call_outcome="negotiating",
            preferred_language="en-US",
            escalation_count=2,
            sentiment_history=["positive", "neutral", "positive"],
        )
        block = RelationshipContextBuilder.build_block(rm)
        # Header is one line, then max _MAX_LINES (5) content lines
        content_lines = [l for l in block.split("\n") if l.startswith("-")]
        assert len(content_lines) <= 5


# ---------------------------------------------------------------------------
# PostCallSummaryRepository tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _CFFI_AVAILABLE, reason="cffi/_cffi_backend not available on this platform")
class TestPostCallSummaryRepository:
    def _make_repo(self):
        from src.libs.repositories.call_summary import PostCallSummaryRepository

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        return PostCallSummaryRepository(mock_conn), mock_cursor

    def test_get_returns_none_when_cursor_empty(self):
        repo, mock_cursor = self._make_repo()
        mock_cursor.fetchone.return_value = None
        result = repo.get("call-1", "tenant-1")
        assert result is None

    def test_save_executes_upsert(self):
        repo, mock_cursor = self._make_repo()
        summary = {
            "call_id": "c1",
            "lead": {"stage": "QUALIFIED", "temperature": "HOT", "qualification_score": 80},
            "requirements": None,
            "conversation": None,
            "sales": None,
            "outcome": "qualified",
            "recommended_next_action": "Offer site visit",
        }
        repo.save("c1", "t1", "cust1", summary)
        assert mock_cursor.execute.call_count >= 1

    def test_security_get_includes_tenant_id_in_query(self):
        repo, mock_cursor = self._make_repo()
        mock_cursor.fetchone.return_value = None
        repo.get("call-X", "tenant-A")
        sql = mock_cursor.execute.call_args[0][0]
        assert "tenant_id" in sql
