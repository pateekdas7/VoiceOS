"""EmergencyRevocation — immediate secret invalidation + re-issue workflow (V4 Ch7 §7.12).

Architecture: V4 Ch7 §7.12 ("a single action invalidates a compromised
secret everywhere"), §7.14 (revocation propagation < 1 min platform-wide —
trivially satisfied in-process since revocation clears the cache synchronously).
"""

from __future__ import annotations

from typing import Any

from src.libs.secrets.manager import SecretsManager
from src.libs.secrets.metrics import record_revocation


class EmergencyRevocation:
    """Invalidates a compromised secret immediately and triggers re-issue."""

    def __init__(self, secrets_manager: SecretsManager, audit_repository: Any | None = None) -> None:
        self._secrets_manager = secrets_manager
        self._audit_repository = audit_repository

    def revoke(self, path: str, reason: str, actor_id: str = "system", tenant_id: str | None = None) -> None:
        """Invalidate ``path`` immediately; a subsequent ``get_secret`` re-fetches (re-issue)."""
        self._secrets_manager.revoke(path, reason)
        record_revocation(path)
        if self._audit_repository is not None and tenant_id is not None:
            self._audit_repository.append(
                tenant_id,
                actor_id=actor_id,
                action="secret_revoked",
                resource_type="secret",
                resource_id=path,
                outcome=f"revoked:{reason}",
            )

    def reissue(self, path: str) -> str:
        """Re-fetch and re-cache ``path`` after revocation, clearing the revoked flag."""
        return self._secrets_manager.rotate(path)
