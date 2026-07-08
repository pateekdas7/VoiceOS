"""Campaign Summary report template (V5 Ch12)."""

from __future__ import annotations

from datetime import datetime

from . import ReportData


def build(
    campaign_name: str,
    calls_completed: int,
    ptp_rate: float,
    contactability_rate: float,
    conversion_rate: float,
    generated_at: datetime,
) -> ReportData:
    return ReportData(
        title=f"Campaign Summary — {campaign_name}",
        columns=("Metric", "Value"),
        rows=(
            ("Calls Completed", calls_completed),
            ("PTP Rate", f"{ptp_rate:.2%}"),
            ("Contactability Rate", f"{contactability_rate:.2%}"),
            ("Conversion Rate", f"{conversion_rate:.2%}"),
        ),
        generated_at=generated_at,
    )


__all__ = ["build"]
