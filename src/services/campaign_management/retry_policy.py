"""RetryPolicyEngine — attempt cadence, max_attempts, backoff (V5 Ch6.4).

Architecture: V5 Ch6 (Campaign Management — Retry Policy).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.libs.contracts.models.campaign import RetryPolicy


class RetryPolicyEngine:
    """Decides whether/when a campaign contact attempt should be retried."""

    @staticmethod
    def should_retry(policy: RetryPolicy, outcome_code: str, attempt_count: int) -> bool:
        """Whether another attempt is permitted after ``outcome_code`` at ``attempt_count`` so far."""
        if attempt_count >= policy.max_attempts:
            return False
        if outcome_code in policy.do_not_retry_on_outcomes:
            return False
        if policy.retry_on_outcomes and outcome_code not in policy.retry_on_outcomes:
            return False
        return True

    @staticmethod
    def next_eligible_at(policy: RetryPolicy, last_attempt_at: datetime) -> datetime:
        """The earliest UTC datetime the next attempt may be made, given ``last_attempt_at``."""
        return last_attempt_at + timedelta(hours=policy.retry_interval_hours)
