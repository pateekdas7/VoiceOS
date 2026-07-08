"""In-memory Vault test double for SecretsManager unit tests.

Real behavioral verification against a live self-hosted HashiCorp Vault
happens in Phase 2 on the CPU node. This fake exists to unit-test
``SecretsManager``/``SecretRotator``/``EmergencyRevocation`` locally without
a running Vault server — it implements the same minimal
``VaultClientProtocol`` surface as ``src.libs.secrets.providers.vault_provider``.

Architecture: V4 Ch7 (Secrets Management); V6 Ch9 (Testing Standards).
"""

from __future__ import annotations


class FakeVaultClient:
    """Records reads/writes against an in-memory KV store, keyed by path."""

    def __init__(self, seed: dict[str, str] | None = None) -> None:
        self._store: dict[str, str] = dict(seed or {})
        self.read_calls: list[str] = []
        self.write_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []

    def read_secret(self, path: str) -> str:
        self.read_calls.append(path)
        if path not in self._store:
            raise KeyError(f"no secret at path: {path}")
        return self._store[path]

    def write_secret(self, path: str, value: str) -> None:
        self.write_calls.append((path, value))
        self._store[path] = value

    def delete_secret(self, path: str) -> None:
        self.delete_calls.append(path)
        self._store.pop(path, None)
