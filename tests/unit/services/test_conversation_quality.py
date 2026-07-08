"""Unit tests for ConversationQualityScorer, ScoringCalibration, and QualityDashboard."""

from __future__ import annotations

from datetime import datetime, timedelta

from src.services.conversation_quality import (
    ConversationQualityScorer,
    QualityDashboard,
    QualityRecord,
    ScoringCalibration,
)


class TestScoringCalibration:
    def test_perfect_score_yields_grade_a(self) -> None:
        cal = ScoringCalibration()
        score = cal.weighted_score(1.0, 1.0, 1.0, 1.0)
        assert cal.grade(score) == "A"

    def test_poor_score_yields_grade_f(self) -> None:
        cal = ScoringCalibration()
        score = cal.weighted_score(0.1, 0.1, 0.1, 0.1)
        assert cal.grade(score) == "F"

    def test_weights_sum_to_one(self) -> None:
        cal = ScoringCalibration()
        total = cal.coherence_weight + cal.policy_compliance_weight + cal.empathy_weight + cal.factual_accuracy_weight
        assert abs(total - 1.0) < 1e-9


class TestConversationQualityScorer:
    def test_conversation_quality_grade_computed(self) -> None:  # required named test
        """test_conversation_quality_grade_computed: grade is computed after recording turns."""
        scorer = ConversationQualityScorer()
        scorer.record_turn_score(
            turn_id="turn-1",
            call_id="call-001",
            coherence=0.9,
            policy_compliance=0.95,
            empathy=0.8,
            factual_accuracy=1.0,
        )
        grade = scorer.compute_grade("call-001")
        assert grade in ("A", "B", "C", "D", "F")

    def test_perfect_turns_yield_grade_a(self) -> None:
        scorer = ConversationQualityScorer()
        for i in range(3):
            scorer.record_turn_score(
                turn_id=f"t-{i}",
                call_id="call-001",
                coherence=1.0,
                policy_compliance=1.0,
                empathy=1.0,
                factual_accuracy=1.0,
            )
        assert scorer.compute_grade("call-001") == "A"

    def test_no_turns_returns_na(self) -> None:
        scorer = ConversationQualityScorer()
        assert scorer.compute_grade("nonexistent-call") == "N/A"

    def test_turn_count_increments(self) -> None:
        scorer = ConversationQualityScorer()
        scorer.record_turn_score("t1", "call-1", 0.9, 0.9, 0.9, 0.9)
        scorer.record_turn_score("t2", "call-1", 0.8, 0.8, 0.8, 0.8)
        assert scorer.get_turn_count("call-1") == 2


class TestQualityDashboard:
    def test_quality_dashboard_trend_api(self) -> None:  # required named test
        """test_quality_dashboard_trend_api: records returned within time window."""
        dashboard = QualityDashboard()
        now = datetime.utcnow()
        dashboard.record("call-1", "tenant-1", "A", 0.93, 3)
        dashboard.record("call-2", "tenant-1", "B", 0.80, 2)

        results = dashboard.get_quality_trend(
            "tenant-1",
            start=now - timedelta(minutes=1),
            end=now + timedelta(minutes=1),
        )
        assert len(results) == 2
        assert all(isinstance(r, QualityRecord) for r in results)

    def test_tenant_isolation(self) -> None:
        dashboard = QualityDashboard()
        now = datetime.utcnow()
        dashboard.record("call-1", "tenant-A", "A", 0.9, 1)
        dashboard.record("call-2", "tenant-B", "B", 0.8, 1)

        results_a = dashboard.get_quality_trend(
            "tenant-A",
            start=now - timedelta(minutes=1),
            end=now + timedelta(minutes=1),
        )
        assert len(results_a) == 1
        assert results_a[0].call_id == "call-1"

    def test_time_range_filter(self) -> None:
        dashboard = QualityDashboard()
        dashboard.record("call-old", "tenant-1", "C", 0.65, 1)
        future = datetime.utcnow() + timedelta(hours=2)

        results = dashboard.get_quality_trend(
            "tenant-1",
            start=future,
            end=future + timedelta(hours=1),
        )
        assert len(results) == 0
