#!/usr/bin/env python3
"""K8s CronJob runner — monthly invoice generation (Phase 6c).

Runs on the 1st of each month at 01:00 UTC (CronJob schedule: 0 1 1 * *).
Generates invoices for the previous calendar month for all tenants with an
active billing subscription. Tenants without a subscription are silently skipped
(ValueError from generate_monthly_invoice is not a job failure).

Each tenant is processed independently. The job exits 1 if any tenant's
invoice generation raised an unexpected error.

Required environment variables:
    POSTGRES_DSN  -- e.g. postgresql://user:pass@host:5432/voiceos

Architecture: V5 Ch10 (Billing Platform — InvoiceGenerator), Phase 6c.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
_log = logging.getLogger("voiceos.jobs.monthly_invoicing")


class _AlwaysPermitPolicy:
    """Stub entitlement policy for the job runner — billing job only generates
    invoices, never checks usage limits; the real PDP is not needed here."""

    def check_entitlement(self, *args: object, **kwargs: object) -> object:
        from src.services.policy_engine.decision import PolicyDecision, PolicyOutcome

        return PolicyDecision(outcome=PolicyOutcome.PERMIT, reason="monthly invoicing job")


def _previous_month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Return (period_start, period_end) for the previous calendar month."""
    if now.month == 1:
        period_start = now.replace(year=now.year - 1, month=12, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        period_start = now.replace(month=now.month - 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    period_end = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return period_start, period_end


def main() -> int:
    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        _log.error("POSTGRES_DSN is required")
        return 1

    try:
        import psycopg2

        from src.libs.repositories.billing import BillingRepository, InvoiceRepository, UsageRepository
        from src.libs.repositories.tenant import TenantRepository
        from src.services.billing.entitlement import EntitlementEngine
        from src.services.billing.invoice import InvoiceGenerator
        from src.services.billing.service import BillingService
        from src.services.billing.subscription import SubscriptionManager

        conn = psycopg2.connect(dsn)
        try:
            now = datetime.now(UTC)
            period_start, period_end = _previous_month_bounds(now)

            billing_service = BillingService(
                SubscriptionManager(BillingRepository(conn)),
                EntitlementEngine(_AlwaysPermitPolicy()),
                InvoiceGenerator(UsageRepository(conn), InvoiceRepository(conn)),
            )
            tenants = TenantRepository(conn).list_all()
            _log.info(
                "Generating invoices for period %s to %s, %d tenants",
                period_start.date(),
                period_end.date(),
                len(tenants),
            )
            invoiced, skipped, errors = 0, 0, 0
            for tenant in tenants:
                try:
                    invoice = billing_service.generate_monthly_invoice(
                        tenant.tenant_id, period_start, period_end
                    )
                    _log.info(
                        "Invoice generated: invoice_id=%s tenant=%s amount=%d %s",
                        invoice.invoice_id,
                        tenant.tenant_id,
                        invoice.total_minor,
                        invoice.currency,
                    )
                    invoiced += 1
                except ValueError:
                    _log.debug("No subscription for tenant=%s — skipping", tenant.tenant_id)
                    skipped += 1
                except Exception:
                    _log.exception("Invoice generation failed for tenant=%s", tenant.tenant_id)
                    errors += 1
            _log.info(
                "Monthly invoicing complete: %d invoiced, %d skipped, %d errors",
                invoiced,
                skipped,
                errors,
            )
        finally:
            conn.close()
        return 0 if errors == 0 else 1
    except Exception:
        _log.exception("Monthly invoicing job setup failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
