"""AuthContext and the authentication vocabulary (V4 Ch5 §5.5 "Authenticated Identity").

Architecture: V4 Ch5 (Authentication).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class AuthMethod(StrEnum):
    """Which credential type produced an :class:`AuthContext` (V4 Ch5 §5.2)."""

    JWT = "JWT"
    """Bearer JWT (OAuth2/OIDC user or service token)."""

    API_KEY = "API_KEY"
    """``X-API-Key`` header — tenant-scoped machine credential."""

    MTLS = "MTLS"
    """Mutual TLS client certificate — service-to-service calls (AR-3)."""


class AuthenticationError(Exception):
    """Raised when a credential fails to validate (maps to HTTP 401 at the PEP)."""


class AuthorizationDeniedError(Exception):
    """Raised when an authenticated subject lacks permission (maps to HTTP 403)."""


class AuthContext(BaseModel):
    """The authenticated identity attached to a request (V4 Ch5 §5.5).

    Populated by :class:`~src.services.auth.middleware.AuthMiddleware` (or
    :class:`~src.services.auth.service.AuthService` when called directly) on
    every successfully authenticated request. Never constructed from
    unauthenticated input — every field here has passed cryptographic or
    lookup verification against an authoritative source (Law of Authority).
    """

    model_config = ConfigDict(frozen=True)

    subject: str
    """The authenticated principal identifier (user id, service name, or API-key owner)."""

    tenant_id: str
    """Tenant scope this identity is bound to (AR-8). Never inferred from request body."""

    role: str = ""
    """RBAC role name (e.g. 'ADMIN', 'AGENT'). Empty for pure service-to-service (mTLS) calls."""

    scopes: tuple[str, ...] = ()
    """OAuth2-style scopes / API-key grants attached to this credential."""

    auth_method: AuthMethod = AuthMethod.JWT
    """Which credential type authenticated this request."""

    expires_at: float | None = None
    """Unix timestamp the credential expires at, if applicable (JWT ``exp``)."""

    api_key_id: str = ""
    """The persisted ``api_keys.api_key_id`` this credential resolved to, when
    ``auth_method == API_KEY`` and the key was Postgres-backed (Sprint-025 Part-3:
    ``api_key_usage`` FK requires the real key id, not the digest-derived ``subject``).
    Empty for in-memory-only keys, JWT, and mTLS."""
