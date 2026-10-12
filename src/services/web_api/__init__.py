"""Web BFF -- the browser-session-authenticated API behind the Admin/Client
dashboards (ADR-005 Sec 4.1).

Deliberately separate from ``src.services.api_platform`` (external,
API-key-authenticated) and from ``src.services.auth``'s ``AuthContext``/
``JWTValidator`` (which validate tenant-scoped JWTs from a tenant's own
OIDC IdP and require ``tenant_id`` on every token). A PlatformActor has no
tenant_id by design (ADR-005 Sec 3), so this module mints and validates its
own VoiceOS-signed session token (see ``session.py``) rather than forcing
PlatformActor through a type that structurally requires a tenant.
"""

from __future__ import annotations
