"""Org Management — Organization/BusinessUnit/Branch hierarchy and scope resolution (V5 Ch2).

Architecture: V5 Ch2 (Multi-Tenant Architecture) §2.4/2.5.
"""

from __future__ import annotations

from .hierarchy import OrgHierarchy
from .models import Branch, BusinessUnit, Organization, OrgScope
from .service import OrgService

__all__ = ["Branch", "BusinessUnit", "OrgHierarchy", "OrgScope", "OrgService", "Organization"]
