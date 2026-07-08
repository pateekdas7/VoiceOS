"""HashiCorp Vault-backed SecretProvider (V4 Ch7 §7.8 "Secret store / vault").

``VaultProvider`` is backend-agnostic over any ``VaultClientProtocol``
implementation — ``FakeVaultClient`` (tests/fixtures) for Phase 1, or
``HVACVaultClient`` (real, self-hosted Vault KV v2 engine over ``hvac``) for
Phase 2 on the CPU node.
"""

from __future__ import annotations

from typing import Protocol


class VaultClientProtocol(Protocol):
    """Minimal Vault KV client surface VaultProvider depends on."""

    def read_secret(self, path: str) -> str: ...

    def write_secret(self, path: str, value: str) -> None: ...

    def delete_secret(self, path: str) -> None: ...


class VaultProvider:
    """SecretProvider that reads/writes a single string value per KV path."""

    def __init__(self, client: VaultClientProtocol) -> None:
        self._client = client

    def get(self, path: str) -> str:
        return self._client.read_secret(path)

    def put(self, path: str, value: str) -> None:
        self._client.write_secret(path, value)

    def delete(self, path: str) -> None:
        self._client.delete_secret(path)


class HVACVaultClient:
    """Real ``VaultClientProtocol`` over a self-hosted Vault KV v2 mount.

    ``hvac`` is imported lazily so importing this module never requires the
    dependency to be installed for Phase 1 (mock-backed) tests — only Phase 2
    deployment code constructs this class.
    """

    def __init__(self, addr: str, token: str, mount_point: str = "secret") -> None:
        import hvac  # intentionally lazy, see class docstring

        self._client = hvac.Client(url=addr, token=token)
        self._mount_point = mount_point

    def read_secret(self, path: str) -> str:
        resp = self._client.secrets.kv.v2.read_secret_version(path=path, mount_point=self._mount_point)
        value: str = resp["data"]["data"]["value"]
        return value

    def write_secret(self, path: str, value: str) -> None:
        self._client.secrets.kv.v2.create_or_update_secret(
            path=path, secret={"value": value}, mount_point=self._mount_point
        )

    def delete_secret(self, path: str) -> None:
        self._client.secrets.kv.v2.delete_metadata_and_all_versions(path=path, mount_point=self._mount_point)
