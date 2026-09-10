"""GoogleOAuthClient -- exchanges an authorization code for a verified email (ADR-005 Sec 4.1/8).

Google's ``access_token`` is an opaque bearer token, not a verifiable JWT --
unlike ``src.services.auth.oidc_provider.OIDCProvider`` (built around
validating a tenant IdP's signed JWT with one static configured public key),
this calls Google's userinfo endpoint directly, the standard OAuth2 pattern
for resolving identity when the caller isn't doing JWKS-based ID-token
verification. Deliberately not built on ``OIDCProvider`` -- that class's
current shape doesn't fit Google's token format, and forcing it in would
mean changing shared auth code for a fit that isn't there.

Architecture: ADR-005 Sec 4.1 (Web BFF), Sec 8 (API & Frontend Architecture).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class GoogleIdentityError(Exception):
    """Raised when the authorization code cannot be exchanged for a verified identity."""


@dataclass(frozen=True)
class GoogleIdentity:
    email: str
    name: str
    email_verified: bool


class GoogleHttpTransport(Protocol):
    """Structural port for the HTTP calls this client makes -- production wiring supplies
    a real ``httpx.AsyncClient``; tests supply a fake (mirrors OIDCProvider's TokenEndpointClient
    precedent, Sprint-018.md "Mock OIDC server")."""

    async def post(self, url: str, *, data: dict[str, str]) -> Any: ...

    async def get(self, url: str, *, headers: dict[str, str]) -> Any: ...


_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
_AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"


class GoogleOAuthClient:
    """Authorization-code exchange + userinfo lookup against Google (ADR-005 Sec 4.1)."""

    def __init__(self, client_id: str, client_secret: str, http_transport: GoogleHttpTransport) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http_transport

    def build_authorize_url(self, *, state: str, redirect_uri: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{_AUTHORIZE_ENDPOINT}?{query}"

    async def resolve_verified_email(self, *, code: str, redirect_uri: str) -> GoogleIdentity:
        """Exchange ``code`` for a token, then resolve the verified identity.

        Raises:
            GoogleIdentityError: On any exchange/lookup failure, or an
                unverified email (never trusts an unverified address).
        """
        try:
            token_response = await self._http.post(
                _TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            token_response.raise_for_status()
            token_body: dict[str, Any] = token_response.json()
        except Exception as exc:
            raise GoogleIdentityError(f"Google token exchange failed: {exc}") from exc

        access_token = token_body.get("access_token")
        if not access_token:
            raise GoogleIdentityError("Google token endpoint response missing access_token")

        try:
            userinfo_response = await self._http.get(
                _USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
            )
            userinfo_response.raise_for_status()
            userinfo: dict[str, Any] = userinfo_response.json()
        except Exception as exc:
            raise GoogleIdentityError(f"Google userinfo lookup failed: {exc}") from exc

        email = userinfo.get("email")
        if not email:
            raise GoogleIdentityError("Google userinfo response missing email")
        if not userinfo.get("email_verified", False):
            raise GoogleIdentityError(f"Google email not verified: {email}")

        return GoogleIdentity(email=email, name=userinfo.get("name", email), email_verified=True)
