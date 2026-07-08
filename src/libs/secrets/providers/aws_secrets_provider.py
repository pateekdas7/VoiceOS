"""AWS Secrets Manager-backed SecretProvider — for a future cloud deployment.

Not wired into the CPU node in Sprint-019 (no AWS account exists for this
project; the CPU node uses ``VaultProvider`` against a self-hosted Vault
instead — see ``vault_provider.py``). Implemented now so
``SecretsManager`` is provider-agnostic from day one, per V4 Ch7's "managed
secret store / vault" being a pluggable backend rather than a single vendor.

Architecture: V4 Ch7 §7.6 ("Secret store: vault (managed secret store / HSM-backed)").
"""

from __future__ import annotations


class AWSSecretsProvider:
    """SecretProvider over AWS Secrets Manager. ``boto3`` is imported lazily."""

    def __init__(self, region_name: str) -> None:
        import boto3  # intentionally lazy, no boto3 dependency for Phase 1/CPU-node use

        self._client = boto3.client("secretsmanager", region_name=region_name)

    def get(self, path: str) -> str:
        resp = self._client.get_secret_value(SecretId=path)
        value: str = resp["SecretString"]
        return value

    def put(self, path: str, value: str) -> None:
        try:
            self._client.put_secret_value(SecretId=path, SecretString=value)
        except self._client.exceptions.ResourceNotFoundException:
            self._client.create_secret(Name=path, SecretString=value)
