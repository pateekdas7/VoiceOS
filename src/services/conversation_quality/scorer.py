"""ConversationQualityScorer — aggregates turn-level quality scores per call.

Accumulates TurnQualityScore records from the OutputEvaluationEngine and
computes call-level quality grades. Non-blocking: the ConversationEngine
fire-and-forgets calls to this scorer.

Architecture: V5 Ch11 (Analytics — conversation quality).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from .calibration import DEFAULT_CALIBRATION, ScoringCalibration

logger = logging.getLogger(__name__)


@dataclass
class _TurnRecord:
    turn_id: str
    coherence: float
    policy_compliance: float
    empathy: float
    factual_accuracy: float


class ConversationQualityScorer:
    """Aggregates per-turn quality scores into per-call quality grades.

    Architecture: V5 Ch11.

    Usage:
        scorer = ConversationQualityScorer()
        scorer.record_turn_score(turn_id, call_id, coherence, policy, empathy, factual)
        grade = scorer.compute_grade(call_id)
    """

    def __init__(self, calibration: ScoringCalibration = DEFAULT_CALIBRATION) -> None:
        self._calibration = calibration
        self._records: dict[str, list[_TurnRecord]] = defaultdict(list)

    def record_turn_score(
        self,
        turn_id: str,
        call_id: str,
        coherence: float,
        policy_compliance: float,
        empathy: float,
        factual_accuracy: float,
    ) -> None:
        """Record quality scores for a single agent turn.

        Args:
            turn_id: Unique turn identifier.
            call_id: Parent call session identifier.
            coherence: Coherence score [0.0, 1.0].
            policy_compliance: Policy compliance score [0.0, 1.0].
            empathy: Empathy score [0.0, 1.0].
            factual_accuracy: Factual accuracy score [0.0, 1.0].
        """
        self._records[call_id].append(
            _TurnRecord(
                turn_id=turn_id,
                coherence=coherence,
                policy_compliance=policy_compliance,
                empathy=empathy,
                factual_accuracy=factual_accuracy,
            )
        )
        logger.debug(
            "ConversationQualityScorer: recorded turn %s for call %s",
            turn_id,
            call_id,
        )

    def compute_grade(self, call_id: str) -> str:
        """Compute the overall quality grade for a call.

        Args:
            call_id: Call session identifier.

        Returns:
            Letter grade: 'A', 'B', 'C', 'D', or 'F'.
            Returns 'N/A' if no turns have been recorded.
        """
        records = self._records.get(call_id, [])
        if not records:
            return "N/A"

        n = len(records)
        avg_coherence = sum(r.coherence for r in records) / n
        avg_policy = sum(r.policy_compliance for r in records) / n
        avg_empathy = sum(r.empathy for r in records) / n
        avg_factual = sum(r.factual_accuracy for r in records) / n

        weighted = self._calibration.weighted_score(avg_coherence, avg_policy, avg_empathy, avg_factual)
        grade = self._calibration.grade(weighted)

        logger.info(
            "ConversationQualityScorer: call %s grade=%s weighted_score=%.3f turns=%d",
            call_id,
            grade,
            weighted,
            n,
        )
        return grade

    def get_turn_count(self, call_id: str) -> int:
        """Return the number of recorded turns for a call."""
        return len(self._records.get(call_id, []))
