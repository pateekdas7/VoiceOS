"""PlatformActor identity and authorization — VoiceOS's own staff (ADR-005 Sec 3/4.2).

Structurally parallel to, and never composed with, ``src.services.authz``
(tenant-scoped RBAC). A PlatformActor is never a TenantActor and vice versa.
"""

from __future__ import annotations
