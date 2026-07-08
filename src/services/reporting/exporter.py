"""ExportService — renders a format-agnostic ``ReportData`` to CSV/XLSX/PDF (V5 Ch12).

Architecture: V5 Ch12 (Reporting Platform — export formats CSV/Excel/PDF/API).
"""

from __future__ import annotations

import csv
import io

from .templates import ReportData

SUPPORTED_FORMATS = ("csv", "xlsx", "pdf")


class UnsupportedExportFormatError(ValueError):
    """Raised when ``ExportService.export()`` is asked for an unknown format."""


class ExportService:
    """Renders a :class:`ReportData` to bytes in the requested format."""

    def export(self, report: ReportData, fmt: str) -> bytes:
        if fmt == "csv":
            return self.to_csv(report)
        if fmt == "xlsx":
            return self.to_xlsx(report)
        if fmt == "pdf":
            return self.to_pdf(report)
        raise UnsupportedExportFormatError(f"unsupported export format {fmt!r}, expected one of {SUPPORTED_FORMATS}")

    def to_csv(self, report: ReportData) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(report.columns)
        writer.writerows(report.rows)
        return buf.getvalue().encode("utf-8")

    def to_xlsx(self, report: ReportData) -> bytes:
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        assert sheet is not None  # Workbook() always creates one default sheet
        sheet.title = report.title[:31] or "Report"  # Excel sheet-name length limit
        sheet.append(list(report.columns))
        for row in report.rows:
            sheet.append(list(row))
        buf = io.BytesIO()
        workbook.save(buf)
        return buf.getvalue()

    def to_pdf(self, report: ReportData) -> bytes:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        page = canvas.Canvas(buf, pagesize=A4)
        _, page_height = A4
        y = page_height - 50

        page.setFont("Helvetica-Bold", 14)
        page.drawString(50, y, report.title)
        y -= 30

        page.setFont("Helvetica-Bold", 10)
        page.drawString(50, y, " | ".join(report.columns))
        y -= 18

        page.setFont("Helvetica", 10)
        for row in report.rows:
            page.drawString(50, y, " | ".join(str(value) for value in row))
            y -= 16
            if y < 50:
                page.showPage()
                page.setFont("Helvetica", 10)
                y = page_height - 50

        page.save()
        return buf.getvalue()


__all__ = ["SUPPORTED_FORMATS", "ExportService", "UnsupportedExportFormatError"]
