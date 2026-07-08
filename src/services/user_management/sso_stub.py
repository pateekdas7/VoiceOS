"""SSOIntegration — explicit stub for Sprint-032's real SAML/OIDC SSO (V5 Ch16).

Sprint-021.md scopes SSO as a deferred stub; Sprint-025 completed without
activating it, and real SSO/SCIM was subsequently scoped into Sprint-032
("Enterprise Platform: SSO, SCIM & Multi-Region") instead. This remains
deliberately not a working implementation. It records tenant SSO
*configuration intent* in the ``sso_config`` table (migration 0018) so
Sprint-032 has a real place to plug into, but every activation attempt
raises ``SSONotImplementedError`` rather than silently pretending to
authenticate a user.

Architecture: V5 Ch16 (Enterprise Platform — SSO).
"""

from __future__ import annotations

from typing import Any


class SSONotImplementedError(NotImplementedError):
    """Raised by any SSO activation attempt — real SSO ships in Sprint-032."""


class SSOIntegration:
    """Records SSO configuration intent; never performs a real SSO handshake."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def configure(self, tenant_id: str, provider: str, config: dict[str, Any]) -> None:
        """Persist SSO configuration for a future Sprint-032 activation. Never enables it."""
        import json

        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO sso_config (tenant_id, provider, enabled, config, updated_at)
            VALUES (%s, %s, FALSE, %s, NOW())
            ON CONFLICT (tenant_id) DO UPDATE SET provider = EXCLUDED.provider, config = EXCLUDED.config, updated_at = NOW()
            """,
            (tenant_id, provider, json.dumps(config)),
        )
        self._conn.commit()

    def authenticate(self, tenant_id: str, assertion: str) -> None:
        """Not implemented until Sprint-032 — always raises."""
        raise SSONotImplementedError("SSO authentication ships in Sprint-032 (V5 Ch16)")
