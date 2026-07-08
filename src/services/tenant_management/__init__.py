"""Tenant Management — multi-tenant lifecycle, isolation, provisioning (V5 Ch2/Ch3).

Architecture: V5 Ch2 (Multi-Tenant Architecture); V5 Ch3 (Tenant Lifecycle).
"""

from __future__ import annotations

from .deletion import TenantDeleter
from .isolation import IsolationProfileManager, UnsafeTenantIdentifierError
from .lifecycle import InvalidTenantTransitionError, TenantLifecycle
from .provisioner import ProvisioningResult, TenantProvisioner
from .service import TenantService
from .suspension import TenantSuspender

__all__ = [
    "InvalidTenantTransitionError",
    "IsolationProfileManager",
    "ProvisioningResult",
    "TenantDeleter",
    "TenantLifecycle",
    "TenantProvisioner",
    "TenantService",
    "TenantSuspender",
    "UnsafeTenantIdentifierError",
]
