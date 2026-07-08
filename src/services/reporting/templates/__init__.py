"""Report templates — Campaign Summary, Collections Performance, Compliance Audit (V5 Ch12).

Each template is a pure function: already-computed metrics in, a
format-agnostic :class:`ReportData` out. ``ExportService`` renders that
``ReportData`` to CSV/XLSX/PDF — templates never know about export formats,
and ``ExportService`` never knows about report semantics.

Architecture: V5 Ch12 (Reporting Platform — report templates).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ReportData:
    """A format-agnostic tabular report: a title, column headers, and rows."""

    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    generated_at: datetime


__all__ = ["ReportData"]
