"""AuditAdminController -- audit log search, compliance reports (V5 Ch13).

Architecture: V5 Ch13 (Administration Portal); V4 Ch9 (Audit).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.libs.contracts.primitives import TenantId
from src.services.reporting.templates import ReportData, compliance_audit

if TYPE_CHECKING:
    from src.libs.audit.event import AuditEvent
    from src.libs.audit.search import AuditSearch
    from src.libs.repositories.admin_audit_view import AdminAuditViewRepository
    from src.services.reporting.exporter import ExportService


class AuditAdminController:
    """Audit log search + compliance-report administration (V5 Ch13).

    ``compliance_report``/``export_compliance_report`` reuse the existing
    Reporting Platform template (``reporting.templates.compliance_audit``,
    V5 Ch12) rather than a second, parallel report format for the Admin
    Portal. ``list_admin_actions`` queries the dedicated ``admin_audit_views``
    read-only VIEW (Sprint-025 Part-3, migration 0025) -- Admin-Portal-
    originated entries only, without re-filtering the full audit trail.
    """

    def __init__(
        self,
        audit_search: AuditSearch,
        export_service: ExportService | None = None,
        admin_audit_view_repository: AdminAuditViewRepository | None = None,
    ) -> None:
        self._search = audit_search
        self._exporter = export_service
        self._admin_views = admin_audit_view_repository

    def by_resource(self, tenant_id: TenantId, resource_type: str, resource_id: str) -> list[AuditEvent]:
        return self._search.by_resource(tenant_id, resource_type, resource_id)

    def in_range(self, tenant_id: TenantId, start: Any = None, end: Any = None) -> list[AuditEvent]:
        return self._search.in_range(tenant_id, start, end)

    def compliance_report(
        self, tenant_id: TenantId, tenant_name: str, start: Any = None, end: Any = None
    ) -> ReportData:
        """Build a compliance-audit report over ``tenant_id``'s audit trail in ``[start, end]``."""
        events = self._search.in_range(tenant_id, start, end)
        return compliance_audit.build(tenant_name, events, datetime.now(UTC))

    def export_compliance_report(
        self, tenant_id: TenantId, tenant_name: str, fmt: str, start: Any = None, end: Any = None
    ) -> bytes:
        """Render the compliance report to ``fmt`` (csv/xlsx/pdf). Raises if no exporter is wired."""
        if self._exporter is None:
            raise ComplianceExportNotConfiguredError("no ExportService wired into this AuditAdminController")
        report = self.compliance_report(tenant_id, tenant_name, start, end)
        return self._exporter.export(report, fmt)

    def list_admin_actions(self, tenant_id: TenantId) -> tuple[AuditEvent, ...]:
        """Admin-Portal-originated audit entries via the ``admin_audit_views`` VIEW.
        Raises if no repository is wired."""
        if self._admin_views is None:
            raise AdminAuditViewNotConfiguredError("no AdminAuditViewRepository wired into this AuditAdminController")
        return self._admin_views.list_for_tenant(tenant_id)


class ComplianceExportNotConfiguredError(RuntimeError):
    """Raised by ``export_compliance_report`` when no :class:`ExportService` backend was supplied."""


class AdminAuditViewNotConfiguredError(RuntimeError):
    """Raised by ``list_admin_actions`` when no :class:`AdminAuditViewRepository` was supplied."""


__all__ = ["AdminAuditViewNotConfiguredError", "AuditAdminController", "ComplianceExportNotConfiguredError"]
