"""WebhookSigner -- HMAC-SHA256 signing for webhook deliveries (V4 Ch12 §12.2, V5 Ch15).

Architecture: V4 Ch12 (API Security -- Signed Webhooks); V5 Ch15 (Integration Platform).
"""

from __future__ import annotations

import hashlib
import hmac
import json

SIGNATURE_HEADER = "X-VoiceOS-Signature"


class WebhookSigner:
    """Computes and verifies the ``X-VoiceOS-Signature: sha256=<hex>`` header (Sprint-025.md)."""

    @staticmethod
    def canonical_payload(payload: dict[str, object]) -> bytes:
        """Deterministic JSON encoding -- signer and verifier must hash the identical bytes."""
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @classmethod
    def sign(cls, payload: dict[str, object], secret: str) -> str:
        """Return the full header value, e.g. ``sha256=3f2b...``."""
        digest = hmac.new(secret.encode("utf-8"), cls.canonical_payload(payload), hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    @classmethod
    def verify(cls, payload: dict[str, object], secret: str, signature_header: str) -> bool:
        """Constant-time comparison against a freshly computed signature."""
        expected = cls.sign(payload, secret)
        return hmac.compare_digest(expected, signature_header)


__all__ = ["SIGNATURE_HEADER", "WebhookSigner"]
