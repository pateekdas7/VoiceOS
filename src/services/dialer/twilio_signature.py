"""Twilio request-signature validation for webhook endpoints.

Implements the algorithm documented at
https://www.twilio.com/docs/usage/security#validating-requests

No dependency on the twilio SDK: HMAC-SHA1 over the full request URL with
POST parameters concatenated in alphabetical order, then base64-encoded,
then constant-time compared with the ``X-Twilio-Signature`` header.

Used by the dialer status-callback route to reject spoofed requests that
would otherwise fake call completions and corrupt lead state.

Architecture: V4 (Security), RI-8 (auth on every ingress).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping


def compute_signature(auth_token: str, url: str, params: Mapping[str, str]) -> str:
    """Return the expected X-Twilio-Signature value for a request.

    Args:
        auth_token: The Twilio account's auth token (never log this).
        url: The full URL Twilio POSTed to, exactly as configured on the
             Twilio side (scheme + host + path + any query string).
        params: The POST form parameters as a plain mapping.

    Returns:
        Base64-encoded HMAC-SHA1 digest, matching what Twilio sends in the
        ``X-Twilio-Signature`` header.
    """
    signed_string = url
    for key in sorted(params.keys()):
        signed_string += key + params[key]
    mac = hmac.new(
        auth_token.encode("utf-8"),
        signed_string.encode("utf-8"),
        hashlib.sha1,
    )
    return base64.b64encode(mac.digest()).decode("utf-8")


def validate_signature(
    auth_token: str,
    url: str,
    params: Mapping[str, str],
    signature_header: str,
) -> bool:
    """Constant-time compare the expected and provided signatures.

    Returns True iff the signature header matches. False on any mismatch or
    when ``signature_header`` is empty. Never raises.
    """
    if not signature_header:
        return False
    expected = compute_signature(auth_token, url, params)
    return hmac.compare_digest(expected, signature_header)


__all__ = ["compute_signature", "validate_signature"]
