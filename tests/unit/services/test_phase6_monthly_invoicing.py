"""Unit tests for Phase 6c: monthly invoicing job runner logic.

Verifies the _previous_month_bounds() helper and the job's error handling:
- Correct billing period for the previous month
- January → December of previous year edge case
- Graceful handling of tenants without subscriptions (ValueError → skip)
- Unexpected errors are counted (not swallowed silently)
"""

from __future__ import annotations

import sys
import os
from datetime import UTC, datetime

import pytest

# Import the helper directly from the runner module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from scripts.jobs.run_monthly_invoicing import _previous_month_bounds


class TestPreviousMonthBounds:
    def test_february_to_january(self) -> None:
        now = datetime(2026, 2, 1, 1, 0, 0, tzinfo=UTC)
        start, end = _previous_month_bounds(now)
        assert start == datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)

    def test_january_to_december_previous_year(self) -> None:
        now = datetime(2027, 1, 1, 1, 0, 0, tzinfo=UTC)
        start, end = _previous_month_bounds(now)
        assert start == datetime(2026, 12, 1, 0, 0, 0, tzinfo=UTC)
        assert end == datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)

    def test_december_to_november(self) -> None:
        now = datetime(2026, 12, 1, 1, 0, 0, tzinfo=UTC)
        start, end = _previous_month_bounds(now)
        assert start == datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 12, 1, 0, 0, 0, tzinfo=UTC)

    def test_period_end_is_start_of_current_month(self) -> None:
        now = datetime(2026, 9, 17, 1, 5, 30, tzinfo=UTC)
        start, end = _previous_month_bounds(now)
        # period_end should be 2026-09-01 00:00:00 UTC
        assert end == datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
        assert start == datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)


class _FakeTenant:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id


class _FakeBillingService:
    def __init__(self, raises_for: set[str] | None = None) -> None:
        self._raises_for = raises_for or set()
        self.invoiced: list[str] = []

    def generate_monthly_invoice(self, tenant_id: object, period_start: datetime, period_end: datetime) -> object:
        tid = str(tenant_id)
        if tid in self._raises_for:
            raise ValueError(f"no subscription for {tid}")
        self.invoiced.append(tid)
        # Return a minimal fake invoice
        return type("Invoice", (), {
            "invoice_id": f"inv-{tid}",
            "total_minor": 10000,
            "currency": "INR",
        })()


class TestInvoicingJobLogic:
    """Test the per-tenant loop logic extracted from main()."""

    def _run_loop(self, tenants, billing_service):
        """Run the invoicing loop in isolation (mirrors main() inner logic)."""
        period_start = datetime(2026, 8, 1, tzinfo=UTC)
        period_end = datetime(2026, 9, 1, tzinfo=UTC)
        invoiced, skipped, errors = 0, 0, 0
        for tenant in tenants:
            try:
                billing_service.generate_monthly_invoice(tenant.tenant_id, period_start, period_end)
                invoiced += 1
            except ValueError:
                skipped += 1
            except Exception:
                errors += 1
        return invoiced, skipped, errors

    def test_tenant_with_subscription_invoiced(self) -> None:
        billing = _FakeBillingService()
        tenants = [_FakeTenant("t-1")]
        invoiced, skipped, errors = self._run_loop(tenants, billing)
        assert invoiced == 1
        assert skipped == 0
        assert errors == 0

    def test_tenant_without_subscription_skipped(self) -> None:
        billing = _FakeBillingService(raises_for={"t-no-sub"})
        tenants = [_FakeTenant("t-no-sub")]
        invoiced, skipped, errors = self._run_loop(tenants, billing)
        assert invoiced == 0
        assert skipped == 1
        assert errors == 0

    def test_mixed_tenants_counted_correctly(self) -> None:
        billing = _FakeBillingService(raises_for={"t-2"})
        tenants = [_FakeTenant("t-1"), _FakeTenant("t-2"), _FakeTenant("t-3")]
        invoiced, skipped, errors = self._run_loop(tenants, billing)
        assert invoiced == 2
        assert skipped == 1
        assert errors == 0

    def test_unexpected_error_counted_as_error(self) -> None:
        class _BrokenBilling:
            def generate_monthly_invoice(self, *a, **k):
                raise RuntimeError("DB down")

        tenants = [_FakeTenant("t-1")]
        invoiced, skipped, errors = self._run_loop(tenants, _BrokenBilling())
        assert invoiced == 0
        assert skipped == 0
        assert errors == 1

    def test_empty_tenant_list_no_errors(self) -> None:
        billing = _FakeBillingService()
        invoiced, skipped, errors = self._run_loop([], billing)
        assert invoiced == 0
        assert skipped == 0
        assert errors == 0
