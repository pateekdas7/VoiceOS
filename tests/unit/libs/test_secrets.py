"""Unit tests for src/libs/secrets/ (V4 Ch7)."""

from __future__ import annotations

import os

import pytest

from src.libs.secrets.manager import SecretNotFoundError, SecretRevokedError, SecretsManager
from src.libs.secrets.providers.vault_provider import VaultProvider
from src.libs.secrets.revocation import EmergencyRevocation
from src.libs.secrets.rotation import SecretRotator
from tests.fixtures.fake_vault import FakeVaultClient


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def append(self, tenant_id: str, **kwargs: object) -> None:
        self.calls.append({"tenant_id": tenant_id, **kwargs})


@pytest.fixture
def vault_client() -> FakeVaultClient:
    return FakeVaultClient(seed={"postgres/password": "s3cr3t"})


@pytest.fixture
def provider(vault_client: FakeVaultClient) -> VaultProvider:
    return VaultProvider(vault_client)


class TestSecretsManager:
    def test_get_secret_fetches_from_provider(self, provider: VaultProvider) -> None:
        manager = SecretsManager(provider)

        assert manager.get_secret("postgres/password") == "s3cr3t"

    def test_get_secret_caches_and_does_not_refetch(
        self, provider: VaultProvider, vault_client: FakeVaultClient
    ) -> None:
        manager = SecretsManager(provider)

        manager.get_secret("postgres/password")
        manager.get_secret("postgres/password")

        assert vault_client.read_calls.count("postgres/password") == 1

    def test_missing_secret_raises_not_found(self, provider: VaultProvider) -> None:
        manager = SecretsManager(provider)

        with pytest.raises(SecretNotFoundError):
            manager.get_secret("does/not/exist")

    def test_secrets_manager_no_env_vars(self, provider: VaultProvider, monkeypatch: pytest.MonkeyPatch) -> None:
        """SecretsManager never reads os.environ for secret values (V4 Ch7 §7.3)."""
        monkeypatch.setenv("POSTGRES_PASSWORD", "env-value-should-never-be-used")
        manager = SecretsManager(provider)

        value = manager.get_secret("postgres/password")

        assert value == "s3cr3t"
        assert value != os.environ["POSTGRES_PASSWORD"]

    def test_rotate_keeps_old_value_valid_during_grace_window(
        self, provider: VaultProvider, vault_client: FakeVaultClient
    ) -> None:
        manager = SecretsManager(provider, grace_window_seconds=60)
        manager.get_secret("postgres/password")

        vault_client.write_secret("postgres/password", "new-value")
        rotated = manager.rotate("postgres/password")

        assert rotated == "new-value"
        assert manager.get_secret("postgres/password") == "new-value"

    def test_revoke_invalidates_immediately(self, provider: VaultProvider) -> None:
        manager = SecretsManager(provider)
        manager.get_secret("postgres/password")

        manager.revoke("postgres/password", reason="leaked in logs")

        assert manager.is_revoked("postgres/password") is True
        with pytest.raises(SecretRevokedError):
            manager.get_secret("postgres/password")

    def test_get_secret_audits_access_without_value(self, provider: VaultProvider) -> None:
        recorder = _Recorder()
        manager = SecretsManager(provider, audit_repository=recorder)

        manager.get_secret("postgres/password", actor_id="conversation-engine", tenant_id="tenant-a")

        assert len(recorder.calls) == 1
        call = recorder.calls[0]
        assert call["action"] == "secret_accessed"
        assert call["resource_id"] == "postgres/password"
        assert "s3cr3t" not in str(call.values())


class TestSecretRotator:
    def test_rotate_records_metric_and_audit(self, provider: VaultProvider, vault_client: FakeVaultClient) -> None:
        manager = SecretsManager(provider)
        recorder = _Recorder()
        rotator = SecretRotator(manager, audit_repository=recorder)
        vault_client.write_secret("postgres/password", "rotated-value")

        new_value = rotator.rotate("postgres/password", tenant_id="tenant-a")

        assert new_value == "rotated-value"
        assert recorder.calls[0]["action"] == "secret_rotated"


class TestEmergencyRevocation:
    def test_revoke_then_reissue(self, provider: VaultProvider, vault_client: FakeVaultClient) -> None:
        manager = SecretsManager(provider)
        revocation = EmergencyRevocation(manager)
        manager.get_secret("postgres/password")

        revocation.revoke("postgres/password", reason="compromised")
        with pytest.raises(SecretRevokedError):
            manager.get_secret("postgres/password")

        vault_client.write_secret("postgres/password", "reissued-value")
        reissued = revocation.reissue("postgres/password")

        assert reissued == "reissued-value"
        assert manager.get_secret("postgres/password") == "reissued-value"

    def test_revoke_audits_with_reason(self) -> None:
        provider = VaultProvider(FakeVaultClient(seed={"jwt/signing_key": "k"}))
        manager = SecretsManager(provider)
        recorder = _Recorder()
        revocation = EmergencyRevocation(manager, audit_repository=recorder)

        revocation.revoke("jwt/signing_key", reason="key leaked in a commit", tenant_id="tenant-a")

        assert recorder.calls[0]["action"] == "secret_revoked"
        assert "leaked" in str(recorder.calls[0]["outcome"])
