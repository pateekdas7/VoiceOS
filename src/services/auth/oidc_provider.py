"""OIDCProvider — authorization-code token exchange with a tenant's OIDC IdP
(V4 Ch5 §5.3 OAuth2/OIDC/JWT).

The HTTP transport is injected via :class:`TokenEndpointClient` (a
structural Protocol) so Phase 1 tests exercise the full exchange→validate
flow against a fake responder instead of a real IdP (Sprint-018.md "Mock
OIDC server (responses fixture)").

Architecture: V4 Ch5 (Authentication — OAuth2/OIDC/JWT).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .jwt_validator import JWTValidator
from .models import AuthContext, AuthenticationError


@runtime_checkable
class TokenEndpointClient(Protocol):
    """Structural protocol for the OIDC token endpoint's HTTP transport."""

    def post(self, url: str, data: dict[str, str]) -> Any:
        """POST the token request; the return value must expose ``.json()``
        (an ``httpx.Response``-compatible shape) and raise on a non-2xx
        status via ``.raise_for_status()``."""
        ...


class OIDCProvider:
    """Exchanges an OAuth2 authorization code for a validated :class:`AuthContext`.

    Args:
        token_endpoint: The IdP's ``/token`` endpoint URL.
        client_id: This service's registered OIDC client id.
        client_secret: This service's registered OIDC client secret.
        http_client: Injected transport (see :class:`TokenEndpointClient`).
            Production wiring supplies a real ``httpx.Client``; tests supply
            a fake that returns a canned token response.
        jwt_validator: Validates the ``id_token``/``access_token`` returned
            by the IdP and extracts the resulting :class:`AuthContext`.
    """

    def __init__(
        self,
        token_endpoint: str,
        client_id: str,
        client_secret: str,
        http_client: TokenEndpointClient,
        jwt_validator: JWTValidator,
    ) -> None:
        self._token_endpoint = token_endpoint
        self._client_id = client_id
        self._client_secret = client_secret
        self._http_client = http_client
        self._jwt_validator = jwt_validator

    def exchange_code(self, code: str, redirect_uri: str) -> AuthContext:
        """Exchange an authorization ``code`` for tokens and validate the result.

        Raises:
            AuthenticationError: On a token-endpoint error or an invalid/expired token.
        """
        try:
            response = self._http_client.post(
                self._token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
            response.raise_for_status()
            token_response: dict[str, Any] = response.json()
        except AuthenticationError:
            raise
        except Exception as exc:
            raise AuthenticationError(f"OIDC token exchange failed: {exc}") from exc

        access_token = token_response.get("access_token")
        if not access_token:
            raise AuthenticationError("OIDC token endpoint response missing access_token")

        return self._jwt_validator.validate(access_token)
