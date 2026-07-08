"""Secrets management — vault-backed runtime secret fetch, rotation, revocation (V4 Ch7).

Architecture: V4 Ch7 (Secrets Management).
"""

from __future__ import annotations

from src.libs.secrets.manager import SecretNotFoundError, SecretProvider, SecretRevokedError, SecretsManager
from src.libs.secrets.revocation import EmergencyRevocation
from src.libs.secrets.rotation import SecretRotator

__all__ = [
    "EmergencyRevocation",
    "SecretNotFoundError",
    "SecretProvider",
    "SecretRevokedError",
    "SecretRotator",
    "SecretsManager",
]
