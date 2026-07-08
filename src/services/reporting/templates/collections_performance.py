"""Collections Performance report template (V5 Ch12)."""

from __future__ import annotations

from datetime import date, datetime

from . import ReportData


def build(
    tenant_name: str,
    day: date,
    calls_completed: int,
    ptp_rate: float,
    recovery_rate: float,
    contactability_rate: float,
    amount_collected_minor: int,
    currency: str,
    generated_at: datetime,
) -> ReportData:
    return ReportData(
        title=f"Collections Performance — {tenant_name} ({day.isoformat()})",
        columns=("Metric", "Value"),
        rows=(
            ("Calls Completed", calls_completed),
            ("PTP Rate", f"{ptp_rate:.2%}"),
            ("Recovery Rate", f"{recovery_rate:.2%}"),
            ("Contactability Rate", f"{contactability_rate:.2%}"),
            ("Amount Collected", f"{amount_collected_minor / 100:.2f} {currency}"),
        ),
        generated_at=generated_at,
    )


__all__ = ["build"]
