"""Unit tests for PromiseToPayService idempotent creation (Sprint-022, V5 Ch4.3).

All tests run fully in-process — no live Postgres required (Phase 1). Real
concurrent-Postgres idempotency verification lives in
tests/integration/services/test_customer_context_assembly.py (Phase 2 also
re-verifies against the CPU node's real Postgres).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.libs.contracts.models.collections import PromiseToPay, PTPStatus
from src.libs.contracts.primitives import CallId, CustomerId, TenantId
from src.libs.repositories.promise_to_pay import PromiseToPayRepository
from src.services.collections.emi_schedule import EMIScheduleService
from src.services.collections.promise_to_pay import PolicyDeniedError, PromiseToPayService, PTPValidationError
from src.services.policy_engine.decision import PolicyDecision, PolicyOutcome
from src.services.policy_engine.rule import PolicyRequest
from tests.fixtures.fake_pg import FakeConnection, FakeCursor

_TENANT = TenantId("tenant-a")
_CALL = CallId("call-1")
_CUSTOMER = CustomerId("cust-1")
_FUTURE = datetime.now(UTC) + timedelta(days=3)


class _FakeEMIScheduleRepository:
    """No unpaid EMIs by default — every PTP amount passes the minimum-amount check."""

    def create_entry(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError

    def get_schedule(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError

    def find_unpaid(self, tenant_id: TenantId, loan_account_id: str) -> tuple[()]:
        return ()

    def update_payment(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError


class _FakePolicyEngineService:
    def __init__(self, outcome: PolicyOutcome = PolicyOutcome.PERMIT) -> None:
        self._outcome = outcome
        self.requests: list[PolicyRequest] = []

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        self.requests.append(request)
        return PolicyDecision(outcome=self._outcome, reason="test")


def _service(policy: _FakePolicyEngineService | None = None) -> PromiseToPayService:
    repo = PromiseToPayRepository(FakeConnection(FakeCursor(fetchone_results=[("ptp-1",)])))
    emi_service = EMIScheduleService(_FakeEMIScheduleRepository())  # type: ignore[arg-type]
    return PromiseToPayService(repo, emi_service, policy_engine_service=policy)  # type: ignore[arg-type]


class TestBuildIdempotencyKey:
    def test_ptp_idempotency_key_deterministic(self) -> None:
        """Required Sprint-022 test: same inputs -> same idempotency key."""
        key1 = PromiseToPayService.build_idempotency_key(_TENANT, _CALL, "loan-1", _FUTURE)
        key2 = PromiseToPayService.build_idempotency_key(_TENANT, _CALL, "loan-1", _FUTURE)

        assert key1 == key2
        assert key1 == f"ptp:{_TENANT}:{_CALL}:loan-1:{_FUTURE.date().isoformat()}"

    def test_key_differs_by_loan_account(self) -> None:
        key1 = PromiseToPayService.build_idempotency_key(_TENANT, _CALL, "loan-1", _FUTURE)
        key2 = PromiseToPayService.build_idempotency_key(_TENANT, _CALL, "loan-2", _FUTURE)

        assert key1 != key2

    def test_key_differs_by_promise_date(self) -> None:
        key1 = PromiseToPayService.build_idempotency_key(_TENANT, _CALL, "loan-1", _FUTURE)
        key2 = PromiseToPayService.build_idempotency_key(_TENANT, _CALL, "loan-1", _FUTURE + timedelta(days=1))

        assert key1 != key2


class TestPromiseToPayServiceCreate:
    async def test_create_without_guard_persists_ptp(self) -> None:
        service = _service()

        ptp = await service.create(_TENANT, _CALL, _CUSTOMER, "loan-1", 50_000_00, "INR", _FUTURE)

        assert isinstance(ptp, PromiseToPay)
        assert ptp.status == PTPStatus.PENDING
        assert ptp.promised_amount_minor == 50_000_00

    async def test_create_rejects_past_promise_date(self) -> None:
        service = _service()
        past = datetime.now(UTC) - timedelta(days=1)

        with pytest.raises(PTPValidationError, match="future"):
            await service.create(_TENANT, _CALL, _CUSTOMER, "loan-1", 50_000_00, "INR", past)

    async def test_create_denied_by_policy_raises(self) -> None:
        policy = _FakePolicyEngineService(outcome=PolicyOutcome.DENY)
        service = _service(policy)

        with pytest.raises(PolicyDeniedError):
            await service.create(_TENANT, _CALL, _CUSTOMER, "loan-1", 50_000_00, "INR", _FUTURE)

    async def test_create_checks_policy_with_collections_domain(self) -> None:
        """Sprint-022 required test: verify PolicyEngine check is called on PTP creation."""
        policy = _FakePolicyEngineService()
        service = _service(policy)

        await service.create(_TENANT, _CALL, _CUSTOMER, "loan-1", 50_000_00, "INR", _FUTURE)

        assert len(policy.requests) == 1
        assert policy.requests[0].domain == "collections"
        assert policy.requests[0].action == "create_ptp"
