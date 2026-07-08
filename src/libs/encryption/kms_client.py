"""KMS client adapters — key-encryption-key (KEK) backends for envelope encryption.

``KMSClientProtocol`` is the interface ``EnvelopeEncryption`` depends on.
Two real backends are provided:

- ``VaultTransitKMSClient`` — self-hosted HashiCorp Vault's Transit secrets
  engine, used as the KMS on the CPU node (Sprint-019 Phase 2; no AWS/GCP
  account exists for this project, mirroring the Sprint-018 self-managed
  mTLS CA precedent).
- ``AWSKMSAdapter`` — for a future cloud deployment. Not wired anywhere in
  Sprint-019; kept so ``EnvelopeEncryption`` is backend-agnostic.

``tests/fixtures/fake_kms.py::FakeKMSClient`` is the Phase 1 test double.

Architecture: V4 Ch8 §8.7 (Public Interfaces), §8.12 (envelope encryption:
"KMS/HSM... DEK encrypts data; KEK... wraps the DEK").
"""

from __future__ import annotations

from typing import Protocol


class KMSClientProtocol(Protocol):
    """KEK operations EnvelopeEncryption depends on."""

    def generate_data_key(self, kek_id: str) -> tuple[bytes, bytes]:
        """Return ``(plaintext_dek, wrapped_dek)`` — a fresh DEK wrapped by ``kek_id``."""
        ...

    def decrypt_data_key(self, kek_id: str, wrapped_dek: bytes) -> bytes:
        """Unwrap ``wrapped_dek`` back to its plaintext DEK using ``kek_id``."""
        ...

    def destroy_kek(self, kek_id: str) -> None:
        """Destroy a KEK — every DEK it ever wrapped becomes permanently unrecoverable."""
        ...

    def ensure_kek(self, kek_id: str) -> None:
        """Create ``kek_id`` if it doesn't already exist; no-op otherwise."""
        ...


class VaultTransitKMSClient:
    """KMSClientProtocol over a self-hosted Vault Transit secrets engine.

    Uses Transit's "generate data key" operation
    (``transit/datakey/plaintext/<kek_id>``), which returns both the
    plaintext DEK and its ciphertext (wrapped) form in one round trip — the
    standard Vault Transit envelope-encryption pattern, avoiding any need
    for a real AWS/GCP KMS account.

    ``hvac`` is imported lazily — only Phase 2 deployment code constructs
    this class.
    """

    def __init__(self, addr: str, token: str, mount_point: str = "transit") -> None:
        import hvac  # intentionally lazy, see class docstring

        self._client = hvac.Client(url=addr, token=token)
        self._mount_point = mount_point

    def _ensure_key(self, kek_id: str) -> None:
        # Probe the specific key rather than listing transit/keys — listing
        # needs a "list" capability on the bare collection path, which the
        # voiceos-app policy deliberately doesn't grant (only per-key
        # wildcard paths); reading/creating one named key is both
        # sufficient and more narrowly scoped.
        import hvac.exceptions

        try:
            self._client.secrets.transit.read_key(name=kek_id, mount_point=self._mount_point)
        except hvac.exceptions.InvalidPath:
            self._client.secrets.transit.create_key(name=kek_id, mount_point=self._mount_point)

    def generate_data_key(self, kek_id: str) -> tuple[bytes, bytes]:
        import base64

        self._ensure_key(kek_id)
        resp = self._client.secrets.transit.generate_data_key(
            name=kek_id, key_type="plaintext", mount_point=self._mount_point
        )
        plaintext_dek = base64.b64decode(resp["data"]["plaintext"])
        wrapped_dek = resp["data"]["ciphertext"].encode("utf-8")
        return plaintext_dek, wrapped_dek

    def decrypt_data_key(self, kek_id: str, wrapped_dek: bytes) -> bytes:
        import base64

        resp = self._client.secrets.transit.decrypt_data(
            name=kek_id, ciphertext=wrapped_dek.decode("utf-8"), mount_point=self._mount_point
        )
        plaintext_dek: bytes = base64.b64decode(resp["data"]["plaintext"])
        return plaintext_dek

    def destroy_kek(self, kek_id: str) -> None:
        self._client.secrets.transit.update_key_configuration(
            name=kek_id, deletion_allowed=True, mount_point=self._mount_point
        )
        self._client.secrets.transit.delete_key(name=kek_id, mount_point=self._mount_point)

    def ensure_kek(self, kek_id: str) -> None:
        self._ensure_key(kek_id)


class AWSKMSAdapter:
    """KMSClientProtocol over AWS KMS ``GenerateDataKey``/``Decrypt`` — for a future cloud deployment.

    Not wired anywhere on the CPU node (no AWS account exists for this
    project). ``boto3`` is imported lazily.
    """

    def __init__(self, region_name: str) -> None:
        import boto3  # intentionally lazy

        self._client = boto3.client("kms", region_name=region_name)

    def generate_data_key(self, kek_id: str) -> tuple[bytes, bytes]:
        resp = self._client.generate_data_key(KeyId=kek_id, KeySpec="AES_256")
        return resp["Plaintext"], resp["CiphertextBlob"]

    def decrypt_data_key(self, kek_id: str, wrapped_dek: bytes) -> bytes:
        resp = self._client.decrypt(CiphertextBlob=wrapped_dek, KeyId=kek_id)
        plaintext: bytes = resp["Plaintext"]
        return plaintext

    def destroy_kek(self, kek_id: str) -> None:
        self._client.schedule_key_deletion(KeyId=kek_id, PendingWindowInDays=7)

    def ensure_kek(self, kek_id: str) -> None:
        import botocore.exceptions

        try:
            self._client.describe_key(KeyId=kek_id)
        except botocore.exceptions.ClientError:
            self._client.create_key(Description=kek_id, KeySpec="SYMMETRIC_DEFAULT")
            self._client.create_alias(AliasName=f"alias/{kek_id}", TargetKeyId=kek_id)
