"""PromiseToPayService — idempotent PTP creation + lifecycle (V5 Ch4.3).

Idempotency key: ``ptp:{tenant_id}:{call_id}:{loan_account_id}:{promise_date}``
(Sprint-022.md §Components). ``IdempotencyGuard.execute_once()`` provides the
exactly-once claim/complete semantics (Sprint-015); ``PromiseToPayRepository
.create_idempotent()``'s own ``uq_ptp_idempotency_key`` DB constraint
(Sprint-014) is passed the same key as a second, independent layer of
defense-in-depth — the same optional-layering precedent as ``CircuitBreaker``
wrapping ``BaseRepository._execute()`` since Sprint-016.

Architecture: V5 Ch4.3 (Promise-To-Pay); V3 Ch8 (Idempotency); Invariant RI-5.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from src.libs.audit.logger import AuditLogger
from src.libs.contracts.models.collections import PromiseToPay, PTPStatus
from src.libs.contracts.primitives import CallId, CustomerId, TenantId
from src.libs.event_bus.publisher import Publisher
from src.libs.idempotency.guard import IdempotencyGuard
from src.libs.repositories.promise_to_pay import PromiseToPayRepository
from src.services.policy_engine.decision import PolicyOutcome
from src.services.policy_engine.rule import PolicyRequest
from src.services.policy_engine.service import PolicyEngineService

from . import metrics
from .emi_schedule import EMIScheduleService

PTP_CREATED_EVENT_TYPE = "saas.ptp.created"
PTP_BROKEN_EVENT_TYPE = "saas.ptp.broken"


class PTPValidationError(ValueError):
    """A PTP request fails a business validation rule (amount/date)."""


class PolicyDeniedError(PermissionError):
    """The Policy Engine denied this PTP creation."""


class PromiseToPayService:
    """Idempotent PTP creation + status lifecycle (V5 Ch4.3)."""

    def __init__(
        self,
        repository: PromiseToPayRepository,
        emi_schedule_service: EMIScheduleService,
        idempotency_guard: IdempotencyGuard | None = None,
        policy_engine_service: PolicyEngineService | None = None,
        publisher: Publisher | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._repo = repository
        self._emi = emi_schedule_service
        self._idempotency_guard = idempotency_guard
        self._policy = policy_engine_service
        self._publisher = publisher
        self._audit_logger = audit_logger

    @staticmethod
    def build_idempotency_key(
        tenant_id: TenantId, call_id: CallId, loan_account_id: str, promise_date: datetime
    ) -> str:
        """Deterministic key: same ``(tenant, call, loan, date)`` always yields the same string."""
        return f"ptp:{tenant_id}:{call_id}:{loan_account_id}:{promise_date.date().isoformat()}"

    async def create(
        self,
        tenant_id: TenantId,
        call_id: CallId,
        customer_id: CustomerId,
        loan_account_id: str,
        promised_amount_minor: int,
        currency: str,
        promise_date: datetime,
        actor_id: str = "conversation_engine",
        notes: str = "",
    ) -> PromiseToPay:
        """Create a PTP exactly once for ``(tenant_id, call_id, loan_account_id, promise_date)``.

        Raises:
            PTPValidationError: ``promise_date`` is not in the future, or
                ``promised_amount_minor`` is below the next unpaid EMI's amount.
            PolicyDeniedError: The Policy Engine denied PTP creation.
        """
        self._validate(tenant_id, loan_account_id, promised_amount_minor, promise_date)
        self._check_policy(tenant_id, call_id, loan_account_id, promised_amount_minor)

        key = self.build_idempotency_key(tenant_id, call_id, loan_account_id, promise_date)

        async def _create_ptp_fn() -> dict[str, object]:
            # IdempotencyRepository.complete() JSON-serializes this return value
            # (json.dumps) — a Pydantic model isn't stdlib-JSON-serializable, so
            # the effect function returns a plain dict, and create() below
            # reconstructs the PromiseToPay from it (works identically whether
            # it came from a fresh insert or the guard's cached-result path).
            now = datetime.now(UTC)
            ptp = PromiseToPay(
                ptp_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                call_id=call_id,
                customer_id=customer_id,
                loan_account_id=loan_account_id,
                promised_amount_minor=promised_amount_minor,
                currency=currency,
                promise_date=promise_date,
                status=PTPStatus.PENDING,
                recorded_at=now,
                updated_at=now,
                notes=notes,
            )
            record, created = self._repo.create_idempotent(ptp, idempotency_key=key)
            if created:
                metrics.record_ptp_created()
                self._emit_created(record, actor_id)
            return record.model_dump(mode="json")

        if self._idempotency_guard is not None:
            result = await self._idempotency_guard.execute_once(tenant_id, key, "ptp", _create_ptp_fn)
        else:
            result = await _create_ptp_fn()
        return PromiseToPay.model_validate(result)

    def update_status(self, tenant_id: TenantId, ptp_id: str, status: PTPStatus) -> None:
        self._repo.update_status(tenant_id, ptp_id, status)
        if status == PTPStatus.KEPT:
            metrics.record_ptp_fulfilled()
        elif status == PTPStatus.BROKEN:
            metrics.record_ptp_broken()
            if self._publisher is not None:
                self._publisher.publish(
                    event_type=PTP_BROKEN_EVENT_TYPE,
                    tenant_id=tenant_id,
                    payload={"tenant_id": str(tenant_id), "ptp_id": ptp_id},
                    correlation_id=ptp_id,
                )

    def find_by_loan(self, tenant_id: TenantId, loan_account_id: str) -> tuple[PromiseToPay, ...]:
        return self._repo.find_by_loan(tenant_id, loan_account_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _validate(
        self, tenant_id: TenantId, loan_account_id: str, promised_amount_minor: int, promise_date: datetime
    ) -> None:
        now = datetime.now(UTC) if promise_date.tzinfo is not None else datetime.utcnow()
        if promise_date <= now:
            raise PTPValidationError("promise_date must be strictly in the future (DocSuite-03)")
        next_emi = self._emi.next_unpaid_emi(tenant_id, loan_account_id)
        if next_emi is not None and promised_amount_minor < next_emi.total_minor:
            raise PTPValidationError(
                f"promised_amount_minor ({promised_amount_minor}) is below the minimum "
                f"required EMI amount ({next_emi.total_minor})"
            )

    def _check_policy(self, tenant_id: TenantId, call_id: CallId, loan_account_id: str, amount_minor: int) -> None:
        if self._policy is None:
            return
        decision = self._policy.evaluate(
            PolicyRequest(
                domain="collections",
                action="create_ptp",
                subject="collections_service",
                resource=loan_account_id,
                tenant_id=str(tenant_id),
                context={"call_id": str(call_id), "amount_minor": amount_minor},
            )
        )
        if decision.outcome in (PolicyOutcome.DENY, PolicyOutcome.FORBID):
            raise PolicyDeniedError(f"PTP creation denied by policy: {decision.reason}")

    def _emit_created(self, ptp: PromiseToPay, actor_id: str) -> None:
        if self._publisher is not None:
            self._publisher.publish(
                event_type=PTP_CREATED_EVENT_TYPE,
                tenant_id=ptp.tenant_id,
                payload={
                    "tenant_id": str(ptp.tenant_id),
                    "call_id": str(ptp.call_id),
                    "customer_id": str(ptp.customer_id),
                    "loan_account_id": ptp.loan_account_id,
                    "promised_amount_minor": ptp.promised_amount_minor,
                    "currency": ptp.currency,
                    "promise_date": ptp.promise_date.isoformat(),
                },
                correlation_id=str(ptp.call_id),
            )
        if self._audit_logger is not None:
            self._audit_logger.record_ptp_created(ptp.tenant_id, actor_id, ptp.ptp_id)
