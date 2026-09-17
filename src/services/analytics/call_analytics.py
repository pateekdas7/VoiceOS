"""CallAnalytics — per-call outcome/duration/conversation-quality metrics (V5 Ch11).

Outcome and duration analytics are backed by the authoritative
``call_dispositions`` table (Postgres). Intent-sequence/negotiation-result/
sentiment-arc are pure aggregation functions over already-fetched sequences
(turn records, decision outcomes, emotion readings) — the Mongo-backed
retrieval of those raw sequences (``call_transcripts``/``decision_envelopes``,
Sprint-002) is the calling layer's concern, not CallAnalytics's; this class
only computes over what it's handed (keeps it testable without a live
MongoDB connection).

Architecture: V5 Ch11 (Analytics Platform — CallAnalytics).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from src.libs.contracts.models.analytics import CallDisposition
from src.libs.contracts.primitives import TenantId

_UNREACHABLE_OUTCOMES = frozenset({"NOT_REACHABLE", "NO_ANSWER"})
_RECOVERY_OUTCOMES = frozenset({"PTP_MADE", "SETTLEMENT_AGREED"})


class CallDispositionRepositoryPort(Protocol):
    def find_between(self, tenant_id: TenantId, start: datetime, end: datetime) -> tuple[CallDisposition, ...]: ...


class LoanAccountRepositoryPort(Protocol):
    """Port for computing average DPD across all tenant loan accounts."""

    def avg_dpd(self, tenant_id: TenantId) -> float:
        """SELECT AVG(dpd) FROM loan_accounts WHERE tenant_id=$1."""
        ...


class CallAnalytics:
    """Per-call outcome/duration analytics, backed by ``call_dispositions``."""

    def __init__(
        self,
        repository: CallDispositionRepositoryPort,
        loan_repository: LoanAccountRepositoryPort | None = None,
    ) -> None:
        self._repository = repository
        self._loans = loan_repository

    def dispositions_between(self, tenant_id: TenantId, start: datetime, end: datetime) -> tuple[CallDisposition, ...]:
        return self._repository.find_between(tenant_id, start, end)

    def outcome_distribution(self, tenant_id: TenantId, start: datetime, end: datetime) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for disposition in self.dispositions_between(tenant_id, start, end):
            counts[disposition.outcome_code] += 1
        return dict(counts)

    def average_duration_ms(self, tenant_id: TenantId, start: datetime, end: datetime) -> float:
        dispositions = self.dispositions_between(tenant_id, start, end)
        if not dispositions:
            return 0.0
        return sum(d.duration_ms for d in dispositions) / len(dispositions)

    def contactability_rate(self, tenant_id: TenantId, start: datetime, end: datetime) -> float:
        dispositions = self.dispositions_between(tenant_id, start, end)
        if not dispositions:
            return 0.0
        contacted = sum(1 for d in dispositions if d.outcome_code not in _UNREACHABLE_OUTCOMES)
        return contacted / len(dispositions)

    def recovery_rate(self, tenant_id: TenantId, start: datetime, end: datetime) -> float:
        dispositions = self.dispositions_between(tenant_id, start, end)
        if not dispositions:
            return 0.0
        recovered = sum(1 for d in dispositions if d.outcome_code in _RECOVERY_OUTCOMES)
        return recovered / len(dispositions)

    def avg_dpd(self, tenant_id: TenantId) -> float:
        """Average days-past-due across all tenant loan accounts."""
        if self._loans is None:
            return 0.0
        return self._loans.avg_dpd(tenant_id)

    @staticmethod
    def intent_sequence(turns: Sequence[Mapping[str, str]]) -> tuple[str, ...]:
        """The ordered intent sequence for a call, given its already-fetched turn records."""
        return tuple(turn["intent"] for turn in turns if turn.get("intent"))

    @staticmethod
    def negotiation_result(decision_outcomes: Sequence[str]) -> str:
        """The call's final negotiation outcome — the last decision in the sequence."""
        return decision_outcomes[-1] if decision_outcomes else "UNKNOWN"

    @staticmethod
    def sentiment_arc(emotion_scores: Sequence[float]) -> tuple[float, ...]:
        """The call's sentiment trajectory, given its already-fetched per-turn emotion scores."""
        return tuple(emotion_scores)


__all__ = ["CallAnalytics", "CallDispositionRepositoryPort", "LoanAccountRepositoryPort"]
