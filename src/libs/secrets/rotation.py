"""SecretRotator — scheduled secret rotation with a grace window (V4 Ch7 §7.11).

Architecture: V4 Ch7 §7.11 (sequence: rotate → new lease → grace window →
retire old), §7.13 (default rotation cadence: provider keys 30d, certs 7d).
"""

from __future__ import annotations

from typing import Any

from src.libs.secrets.manager import SecretsManager
from src.libs.secrets.metrics import record_rotation


class SecretRotator:
    """Rotates a secret via its SecretsManager, auditing the event."""

    def __init__(self, secrets_manager: SecretsManager, audit_repository: Any | None = None) -> None:
        self._secrets_manager = secrets_manager
        self._audit_repository = audit_repository

    def rotate(self, path: str, actor_id: str = "system", tenant_id: str | None = None) -> str:
        """Issue a new value for ``path``; the old value stays valid for the grace window."""
        new_value = self._secrets_manager.rotate(path)
        record_rotation(path)
        if self._audit_repository is not None and tenant_id is not None:
            self._audit_repository.append(
                tenant_id,
                actor_id=actor_id,
                action="secret_rotated",
                resource_type="secret",
                resource_id=path,
                outcome="success",
            )
        return new_value
