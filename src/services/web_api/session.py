"""WebSession -- the BFF's own signed session token (ADR-005 Sec 3/4.1).

After a browser completes IdP sign-in (Google, or a tenant's own configured
IdP), the BFF looks up which internal actor the verified email belongs to
and mints its *own* VoiceOS-signed session token -- it does not forward the
IdP's token to the browser. This token carries ``actor_kind`` so a single
cookie can never be ambiguous about which side of the Actor Model boundary
(ADR-005 Sec 3) it belongs to.

Uses PyJWT + `cryptography` directly (both already project dependencies via
``src.services.auth.jwt_validator``) rather than introducing a new signing
library. This is intentionally a separate signing key from any tenant IdP's
key -- the BFF is the only thing that can mint a WebSession, and the only
thing that needs to verify one.
"""

from __future__ import annotations

import time
from typing import Literal

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from pydantic import BaseModel, ConfigDict

_ALGORITHM = "RS256"
_ISSUER = "voiceos-web-bff"

ActorKind = Literal["platform", "tenant"]


class WebSession(BaseModel):
    """The authenticated browser identity for one request (ADR-005 Sec 3)."""

    model_config = ConfigDict(frozen=True)

    actor_kind: ActorKind
    subject: str
    """platform_user_id (actor_kind="platform") or user_id (actor_kind="tenant")."""
    tenant_id: str | None = None
    """Always None for actor_kind="platform" (ADR-005 Sec 3 -- never inferred, never defaulted)."""
    role: str
    """Display/human-readable role name. Platform sessions: a PlatformRole value,
    checked via PlatformRBACEngine. Tenant sessions: the assigned Role.name --
    NOT itself used for permission checks (see ``permissions`` below)."""
    permissions: tuple[str, ...] = ()
    """For actor_kind="tenant": the assigned Role's actual ``permissions`` column,
    read from the roles table at login time (Law of Authority -- the DB row is
    authoritative, never a hardcoded mirror). Baked into the signed token at
    issuance so authorization checks don't need a DB round-trip per request;
    a permission change takes effect on the assignee's next login. Unused
    (empty) for actor_kind="platform", which checks PlatformRole via
    PlatformRBACEngine instead -- platform roles have no DB-backed custom
    permission system to source from."""
    email: str
    expires_at: float


class WebSessionCodec:
    """Signs and verifies :class:`WebSession` tokens (ADR-005 Sec 4.1)."""

    def __init__(self, private_key: RSAPrivateKey, public_key: RSAPublicKey, ttl_seconds: int = 8 * 3600) -> None:
        self._private_key = private_key
        self._public_key = public_key
        self._ttl_seconds = ttl_seconds

    def encode(
        self,
        *,
        actor_kind: ActorKind,
        subject: str,
        role: str,
        email: str,
        tenant_id: str | None,
        permissions: tuple[str, ...] = (),
    ) -> str:
        """Mint a signed session token. Refuses to encode an inconsistent actor/tenant pairing."""
        if actor_kind == "platform" and tenant_id is not None:
            raise ValueError("a platform actor session must never carry a tenant_id (ADR-005 Sec 3)")
        if actor_kind == "tenant" and tenant_id is None:
            raise ValueError("a tenant actor session must always carry a tenant_id")

        now = time.time()
        expires_at = now + self._ttl_seconds
        payload = {
            "iss": _ISSUER,
            "iat": now,
            "exp": expires_at,
            "actor_kind": actor_kind,
            "sub": subject,
            "role": role,
            "permissions": list(permissions),
            "email": email,
            "tenant_id": tenant_id,
        }
        return jwt.encode(payload, key=self._private_key, algorithm=_ALGORITHM)

    def decode(self, token: str) -> WebSession | None:
        """Validate and parse a session token. Never raises -- returns None on any failure."""
        try:
            claims = jwt.decode(
                token,
                key=self._public_key,
                algorithms=[_ALGORITHM],
                issuer=_ISSUER,
                options={"require": ["iss", "exp", "actor_kind", "sub", "role", "email"]},
            )
        except jwt.PyJWTError:
            return None

        actor_kind = claims.get("actor_kind")
        if actor_kind not in ("platform", "tenant"):
            return None

        return WebSession(
            actor_kind=actor_kind,
            subject=str(claims["sub"]),
            tenant_id=claims.get("tenant_id"),
            role=str(claims["role"]),
            permissions=tuple(claims.get("permissions", ())),
            email=str(claims["email"]),
            expires_at=float(claims["exp"]),
        )
