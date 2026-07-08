"""JWTValidator — validates signed JWTs and extracts claims (V4 Ch5 §5.3 OAuth2/OIDC/JWT).

Uses RS256 (asymmetric) signatures exclusively — the validator only ever
holds a *public* key, never a signing secret, so a compromised service
process cannot mint tokens (V4 Ch5 §5.8 key management).

Architecture: V4 Ch5 (Authentication — OAuth2/OIDC/JWT).
"""

from __future__ import annotations

import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

from .models import AuthContext, AuthenticationError, AuthMethod

_ALGORITHM = "RS256"

_REQUIRED_CLAIMS = ("sub", "tenant_id", "exp")


class JWTValidator:
    """Validates RS256-signed JWTs and extracts an :class:`AuthContext`.

    Args:
        public_key: The RSA public key used to verify token signatures.
        issuer: Expected ``iss`` claim; ``None`` skips issuer verification
            (Phase 1 / mock-IdP testing only — production wiring always
            supplies the tenant IdP's issuer, V4 Ch5 §5.3).
        audience: Expected ``aud`` claim; ``None`` skips audience verification.
        leeway_seconds: Clock-skew tolerance applied to ``exp``/``nbf`` checks.
    """

    def __init__(
        self,
        public_key: RSAPublicKey,
        issuer: str | None = None,
        audience: str | None = None,
        leeway_seconds: int = 0,
    ) -> None:
        self._public_key = public_key
        self._issuer = issuer
        self._audience = audience
        self._leeway_seconds = leeway_seconds

    def validate(self, token: str) -> AuthContext:
        """Validate ``token``'s signature and expiry, and extract its claims.

        Raises:
            AuthenticationError: On any signature, expiry, or missing-claim
                failure. The PEP (middleware) maps this to HTTP 401.
        """
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=self._public_key,
                algorithms=[_ALGORITHM],
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway_seconds,
                options={"require": list(_REQUIRED_CLAIMS)},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"JWT validation failed: {exc}") from exc

        return AuthContext(
            subject=str(claims["sub"]),
            tenant_id=str(claims["tenant_id"]),
            role=str(claims.get("role", "")),
            scopes=tuple(claims.get("scopes", ())),
            auth_method=AuthMethod.JWT,
            expires_at=float(claims["exp"]),
        )


def issue_test_token(
    private_key: RSAPrivateKey,
    subject: str,
    tenant_id: str,
    role: str = "",
    scopes: tuple[str, ...] = (),
    expires_in_seconds: int = 3600,
    issuer: str | None = None,
    audience: str | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Issue an RS256 JWT for tests/Phase-1 mock validation.

    Not a production code path — real tokens are minted by the tenant's
    OIDC IdP (see :mod:`oidc_provider`). This exists so Phase 1 tests can
    generate valid/expired/tampered tokens against an in-process RSA key
    pair with no real IdP (Sprint-018.md "Mock Backends Used").
    """
    now = time.time()
    payload: dict[str, Any] = {
        "sub": subject,
        "tenant_id": tenant_id,
        "role": role,
        "scopes": list(scopes),
        "iat": now,
        "exp": now + expires_in_seconds,
    }
    if issuer is not None:
        payload["iss"] = issuer
    if audience is not None:
        payload["aud"] = audience
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, key=private_key, algorithm=_ALGORITHM)
