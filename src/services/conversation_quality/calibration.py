"""ScoringCalibration — weights and thresholds for quality grading.

Architecture: V5 Ch11 (Analytics — conversation quality).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoringCalibration:
    """Weights for the four quality dimensions.

    All weights must sum to 1.0. The grade thresholds map average weighted
    scores to letter grades (A/B/C/D/F).
    """

    coherence_weight: float = 0.20
    policy_compliance_weight: float = 0.30
    empathy_weight: float = 0.20
    factual_accuracy_weight: float = 0.30

    grade_a_threshold: float = 0.90
    grade_b_threshold: float = 0.75
    grade_c_threshold: float = 0.60
    grade_d_threshold: float = 0.45

    def weighted_score(
        self,
        coherence: float,
        policy_compliance: float,
        empathy: float,
        factual_accuracy: float,
    ) -> float:
        """Compute the weighted aggregate quality score."""
        return (
            coherence * self.coherence_weight
            + policy_compliance * self.policy_compliance_weight
            + empathy * self.empathy_weight
            + factual_accuracy * self.factual_accuracy_weight
        )

    def grade(self, weighted_score: float) -> str:
        """Convert a weighted score to a letter grade."""
        if weighted_score >= self.grade_a_threshold:
            return "A"
        if weighted_score >= self.grade_b_threshold:
            return "B"
        if weighted_score >= self.grade_c_threshold:
            return "C"
        if weighted_score >= self.grade_d_threshold:
            return "D"
        return "F"


DEFAULT_CALIBRATION = ScoringCalibration()
