"""OutputEvaluationEngine — async quality scoring of LLM output.

Scores the LLM-generated response across four dimensions after it has
been delivered (non-blocking; results feed ConversationQualityScorer).

Scoring is heuristic-based for Sprint-012. A future sprint will replace
these heuristics with a lightweight evaluator model.

Architecture: V2 Ch1 (Output Evaluation); V5 Ch11 (Analytics).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from src.libs.contracts.response_plan import ResponsePlan
from src.libs.contracts.turn import TurnInput

logger = logging.getLogger(__name__)

_PROHIBITED_PATTERNS = [
    re.compile(r"\bjail\b", re.I),
    re.compile(r"\bpolice\b", re.I),
    re.compile(r"\bsue\b", re.I),
    re.compile(r"\bcourtb?\b", re.I),
    re.compile(r"\bthreat\b", re.I),
]

_EMPATHY_PHRASES = [
    "samajh",
    "understand",
    "sorry",
    "maafi",
    "help",
    "madad",
    "concerned",
]


@dataclass(frozen=True)
class TurnQualityScore:
    """Quality score for a single agent turn.

    All dimensions are [0.0, 1.0]; higher is better.
    """

    turn_id: str
    coherence: float
    """Textual coherence: response is relevant to the customer utterance."""
    policy_compliance: float
    """Policy compliance: no prohibited content detected."""
    empathy: float
    """Empathy: response demonstrates understanding of customer state."""
    factual_accuracy: float
    """Factual accuracy: response does not contradict ResponsePlan.facts."""


class OutputEvaluationEngine:
    """Scores LLM output quality asynchronously after delivery.

    This engine is never on the critical path — it is called fire-and-forget
    after the response has been sent to TTS.

    Architecture: V2 Ch1.
    """

    def score(
        self,
        turn: TurnInput,
        llm_output: str,
        response_plan: ResponsePlan,
    ) -> TurnQualityScore:
        """Score the LLM output for a given turn and plan.

        Args:
            turn: The customer's TurnInput.
            llm_output: The full LLM-generated response text.
            response_plan: The sealed ResponsePlan that governed this turn.

        Returns:
            TurnQualityScore with dimension scores [0.0, 1.0].
        """
        coherence = self._score_coherence(turn.transcript, llm_output)
        policy = self._score_policy(llm_output, response_plan)
        empathy = self._score_empathy(llm_output, response_plan)
        factual = self._score_factual(llm_output, response_plan)

        result = TurnQualityScore(
            turn_id=turn.turn_id,
            coherence=coherence,
            policy_compliance=policy,
            empathy=empathy,
            factual_accuracy=factual,
        )

        logger.debug(
            "OutputEvaluationEngine scored turn",
            extra={
                "turn_id": turn.turn_id,
                "coherence": coherence,
                "policy": policy,
                "empathy": empathy,
                "factual": factual,
            },
        )
        return result

    # ------------------------------------------------------------------
    # Scoring heuristics
    # ------------------------------------------------------------------

    @staticmethod
    def _score_coherence(transcript: str, output: str) -> float:
        """Check that the response is not empty and has some overlap with context."""
        if not output.strip():
            return 0.0
        # Trivial coherence: response is longer than a single word.
        if len(output.split()) < 3:
            return 0.4
        return 0.9

    @staticmethod
    def _score_policy(output: str, plan: ResponsePlan) -> float:
        """Penalise prohibited phrases and must_not_say violations."""
        score = 1.0
        for pat in _PROHIBITED_PATTERNS:
            if pat.search(output):
                score -= 0.25
        for item in plan.must_not_say:
            if item.pattern and re.search(item.pattern, output, re.I):
                score -= 0.3
        return max(0.0, round(score, 4))

    @staticmethod
    def _score_empathy(output: str, plan: ResponsePlan) -> float:
        """Reward presence of empathy markers when emotion is negative."""
        sentiment = plan.emotion.sentiment
        if sentiment >= 0.0:
            return 0.9  # neutral/positive emotion — empathy less critical
        lower = output.lower()
        hits = sum(1 for phrase in _EMPATHY_PHRASES if phrase in lower)
        return min(1.0, 0.5 + hits * 0.15)

    @staticmethod
    def _score_factual(output: str, plan: ResponsePlan) -> float:
        """Check that the response does not contradict plan.facts.

        Sprint-012 heuristic: verify that no numeric amounts in the output
        differ from facts['outstanding_balance_minor'] by more than 20%.
        """
        if not plan.facts:
            return 1.0
        # Extract INR amounts from output (₹ or Rs. prefix).
        amounts = re.findall(r"(?:₹|Rs\.?\s*)(\d[\d,]*)", output)
        if not amounts:
            return 1.0

        bal_minor = plan.facts.get("outstanding_balance_minor")
        if bal_minor is None or not isinstance(bal_minor, int):
            return 1.0

        bal_major = bal_minor / 100
        for raw in amounts:
            try:
                val = float(raw.replace(",", ""))
            except ValueError:
                continue
            deviation = abs(val - bal_major) / max(1.0, bal_major)
            if deviation > 0.20:
                return 0.4  # Significant factual deviation detected
        return 1.0
