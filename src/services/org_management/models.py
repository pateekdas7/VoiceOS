"""Org hierarchy models — re-exported from the Sprint-002/014 canonical contracts.

``Organization``/``BusinessUnit``/``Branch`` (and the RBAC-scoping
``OrgScope``) already exist as frozen Pydantic contract types, backed by
the ``organizations``/``business_units``/``branches`` Postgres tables
(migration 0002). This module intentionally does not redefine them — it
re-exports the canonical types so callers can import from
``src.services.org_management.models`` per Sprint-021.md's file layout
without a duplicate, divergent definition.

Architecture: V5 Ch2 (Multi-Tenant Architecture — org hierarchy).
"""

from __future__ import annotations

from src.libs.contracts.models.tenant import Branch, BusinessUnit, Organization
from src.libs.contracts.models.user import OrgScope

__all__ = ["Branch", "BusinessUnit", "OrgScope", "Organization"]
