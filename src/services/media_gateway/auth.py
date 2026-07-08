"""Connection authentication for the Media Gateway.

Implements carrier-specific credential validation for Twilio WebSocket and
SIP/RTP transports.  Authentication always completes before any session
resource is allocated (AR-2: auth-before-allocation).

Twilio: HMAC-SHA1 webhook signature validation (Twilio Security documentation).
SIP:    From-header prefix matching against a caller allow-list.

Architecture: V1 Ch3.3 (auth-before-allocation); V6 Ch4 AR-2; V4 Ch2 (security).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from src.services.media_gateway.protocol import AuthResult

# ---------------------------------------------------------------------------
# Twilio HMAC-SHA1 signature validation
# ---------------------------------------------------------------------------


def validate_twilio_signature(
    auth_token: str,
    url: str,
    params: dict[str, str],
    signature: str,
) -> bool:
    """Validate a Twilio webhook request signature.

    Twilio signs every webhook with HMAC-SHA1 using the account auth token.
    The signature covers the full request URL concatenated with the
    alphabetically-sorted POST parameter key-value pairs.

    Reference: https://www.twilio.com/docs/usage/webhooks/webhooks-security

    Args:
        auth_token: The Twilio account auth token (secret — never log this).
        url: The full request URL as Twilio sent it (scheme + host + path + query).
        params: POST form parameters from the webhook request body.
        signature: The X-Twilio-Signature header value from the request.

    Returns:
        True when the signature is valid (credentials authentic).
    """
    sorted_params = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    payload = (url + sorted_params).encode()
    mac = hmac.new(auth_token.encode(), payload, hashlib.sha1)
    expected = base64.b64encode(mac.digest()).decode()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# ConnectionAuthenticator
# ---------------------------------------------------------------------------


class ConnectionAuthenticator:
    """Validates carrier credentials before any session resource is allocated.

    One instance is shared by the MediaGatewayService.  All methods are
    pure functions (no I/O, no shared mutable state) so the authenticator is
    safe to call from concurrent async tasks.

    Architecture: V1 Ch3.3; V6 Ch4 AR-2.
    """

    def authenticate_twilio(self, credentials: dict[str, str]) -> AuthResult:
        """Validate an inbound Twilio Media Streams connection.

        Expected keys in ``credentials``:
            account_sid         — Twilio AccountSid to verify
            auth_token          — Twilio auth token for HMAC signing
            url                 — Full webhook URL (used in HMAC computation)
            params              — JSON-encoded POST params dict (may be '{}')
            x_twilio_signature  — X-Twilio-Signature header from the request
            expected_account_sid — AccountSid this service instance expects

        Returns:
            AuthResult(success=True) when all checks pass.
            AuthResult(success=False, reason=...) on any failure.
        """
        try:
            account_sid = credentials.get("account_sid", "")
            auth_token = credentials.get("auth_token", "")
            url = credentials.get("url", "")
            raw_params = credentials.get("params", "{}")
            signature = credentials.get("x_twilio_signature", "")
            expected_sid = credentials.get("expected_account_sid", "")

            if not all([account_sid, auth_token, url, signature]):
                return AuthResult(success=False, reason="missing_required_fields")

            if expected_sid and account_sid != expected_sid:
                return AuthResult(
                    success=False,
                    reason=f"account_sid_mismatch: got {account_sid!r}",
                )

            try:
                params: dict[str, str] = json.loads(raw_params)
            except json.JSONDecodeError:
                return AuthResult(success=False, reason="invalid_params_json")

            if not validate_twilio_signature(auth_token, url, params, signature):
                return AuthResult(success=False, reason="invalid_signature")

            return AuthResult(success=True)

        except Exception as exc:
            return AuthResult(success=False, reason=f"authentication_error: {exc}")

    def authenticate_sip(self, credentials: dict[str, str]) -> AuthResult:
        """Validate a SIP INVITE connection.

        Expected keys in ``credentials``:
            from_header          — SIP From header value from the parsed INVITE
            allowed_from_prefix  — Allow-list prefix (e.g. 'sip:+91')
                                   Empty string means accept all callers.

        Returns:
            AuthResult(success=True) when the From header matches the allow-list.
            AuthResult(success=False, reason=...) otherwise.
        """
        try:
            from_header = credentials.get("from_header", "")
            allowed_prefix = credentials.get("allowed_from_prefix", "")

            if not from_header:
                return AuthResult(success=False, reason="missing_from_header")

            if allowed_prefix and not from_header.startswith(allowed_prefix):
                return AuthResult(
                    success=False,
                    reason=f"from_header_not_allowed: {from_header!r}",
                )

            return AuthResult(success=True)

        except Exception as exc:
            return AuthResult(success=False, reason=f"authentication_error: {exc}")
