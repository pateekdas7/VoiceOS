"""ScheduleEngine — RBI-compliant call scheduling + retry (V5 Ch6; V4 Ch2).

Delegates calling-hours/frequency enforcement to the existing Sprint-017
``PolicyEngineService.check_call_admission()`` (``RBIPolicyPack.CALLING_HOURS``/
``CALLING_FREQUENCY``) rather than re-deriving the 08:00-20:00/3-calls-per-day
numbers here — see Sprint-023 pre-execution review (those numbers live in
code, not in Volume 4's architecture prose).

Architecture: V5 Ch6 (Campaign Management — Scheduling); V4 Ch2 (RBI calling hours).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from src.libs.contracts.models.campaign import RetryPolicy
from src.libs.contracts.primitives import TenantId
from src.services.policy_engine.decision import PolicyDecision, PolicyOutcome

from .retry_policy import RetryPolicyEngine


@runtime_checkable
class DNDStatusPort(Protocol):
    """Structural port for a do-not-disturb lookup (no DND concept exists elsewhere yet — Sprint-023 finding)."""

    def is_on_dnd(self, tenant_id: TenantId, customer_id: str) -> bool: ...


@runtime_checkable
class CallAdmissionPort(Protocol):
    """Structural port matching ``PolicyEngineService.check_call_admission()``.

    Typed as a Protocol (not the concrete ``PolicyEngineService``) so unit
    tests can inject ``FakePolicyEngineService`` (``tests/fixtures/policy.py``)
    without a real Postgres/Redis-backed PDP — same convention as
    ``DNDStatusPort`` above.
    """

    def check_call_admission(
        self,
        tenant_id: str,
        call_id: str,
        subject: str = "conversation_engine",
        hour: int | None = None,
        calls_today_count: int = 0,
    ) -> PolicyDecision: ...


class ScheduleEngine:
    """Decides whether/when the next call attempt to a customer may be dialled."""

    def __init__(
        self,
        policy_engine_service: CallAdmissionPort,
        dnd_checker: DNDStatusPort | None = None,
    ) -> None:
        self._policy_engine_service = policy_engine_service
        self._dnd_checker = dnd_checker

    def schedule_next_call(
        self,
        tenant_id: TenantId,
        customer_id: str,
        campaign_id: str,
        *,
        hour: int,
        calls_today_count: int = 0,
        attempt_count: int = 0,
        retry_policy: RetryPolicy | None = None,
        last_outcome_code: str | None = None,
        last_attempt_at: datetime | None = None,
    ) -> datetime | None:
        """Return the next permitted dial datetime, or ``None`` if this contact may not be dialled now.

        Returns ``None`` (never raises) when: the customer is on DND, the
        retry policy's ``max_attempts``/``do_not_retry_on_outcomes`` have
        been exhausted, or the Policy Engine denies admission (outside
        calling hours, or the daily frequency cap has been reached).
        """
        if self._dnd_checker is not None and self._dnd_checker.is_on_dnd(tenant_id, customer_id):
            return None

        if retry_policy is not None:
            if attempt_count >= retry_policy.max_attempts:
                return None
            if last_outcome_code is not None and last_outcome_code in retry_policy.do_not_retry_on_outcomes:
                return None

        decision = self._policy_engine_service.check_call_admission(
            tenant_id=str(tenant_id),
            call_id=f"{campaign_id}:{customer_id}",
            subject="campaign_management",
            hour=hour,
            calls_today_count=calls_today_count,
        )
        if decision.outcome != PolicyOutcome.PERMIT:
            return None

        if retry_policy is not None and last_attempt_at is not None:
            return RetryPolicyEngine.next_eligible_at(retry_policy, last_attempt_at)
        return datetime.now(UTC)
