"""Unit tests for GoogleOAuthClient (ADR-005 Sec 4.1/8).

All tests run fully in-process against a fake HTTP transport -- no live
network required (Phase 1), mirroring OIDCProvider's TokenEndpointClient
fake-transport test precedent.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.services.web_api.google_oauth import GoogleIdentityError, GoogleOAuthClient


class _FakeResponse:
    def __init__(self, json_body: dict[str, Any], status_ok: bool = True) -> None:
        self._json_body = json_body
        self._status_ok = status_ok

    def raise_for_status(self) -> None:
        if not self._status_ok:
            raise RuntimeError("simulated non-2xx response")

    def json(self) -> dict[str, Any]:
        return self._json_body


class _FakeTransport:
    def __init__(self, token_response: dict[str, Any], userinfo_response: dict[str, Any]) -> None:
        self._token_response = token_response
        self._userinfo_response = userinfo_response
        self.posted_data: dict[str, str] | None = None
        self.get_headers: dict[str, str] | None = None

    async def post(self, url: str, data: dict[str, str]) -> _FakeResponse:
        self.posted_data = data
        return _FakeResponse(self._token_response)

    async def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        self.get_headers = headers
        return _FakeResponse(self._userinfo_response)


class TestBuildAuthorizeUrl:
    def test_includes_client_id_state_and_redirect_uri(self) -> None:
        client = GoogleOAuthClient("client-123", "secret", _FakeTransport({}, {}))
        url = client.build_authorize_url(state="abc", redirect_uri="https://bff.example.com/auth/google/callback")
        assert "client_id=client-123" in url
        assert "state=abc" in url
        assert "redirect_uri=https://bff.example.com/auth/google/callback" in url
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")


class TestResolveVerifiedEmail:
    async def test_returns_identity_on_success(self) -> None:
        transport = _FakeTransport(
            token_response={"access_token": "tok-1"},
            userinfo_response={"email": "alice@example.com", "name": "Alice", "email_verified": True},
        )
        client = GoogleOAuthClient("client-id", "secret", transport)
        identity = await client.resolve_verified_email(code="auth-code", redirect_uri="https://bff/callback")
        assert identity.email == "alice@example.com"
        assert identity.name == "Alice"
        assert identity.email_verified is True
        assert transport.get_headers == {"Authorization": "Bearer tok-1"}

    async def test_raises_when_token_exchange_missing_access_token(self) -> None:
        transport = _FakeTransport(token_response={}, userinfo_response={})
        client = GoogleOAuthClient("client-id", "secret", transport)
        with pytest.raises(GoogleIdentityError, match="missing access_token"):
            await client.resolve_verified_email(code="c", redirect_uri="https://bff/callback")

    async def test_raises_when_userinfo_missing_email(self) -> None:
        transport = _FakeTransport(
            token_response={"access_token": "tok-1"}, userinfo_response={"email_verified": True}
        )
        client = GoogleOAuthClient("client-id", "secret", transport)
        with pytest.raises(GoogleIdentityError, match="missing email"):
            await client.resolve_verified_email(code="c", redirect_uri="https://bff/callback")

    async def test_raises_when_email_not_verified(self) -> None:
        transport = _FakeTransport(
            token_response={"access_token": "tok-1"},
            userinfo_response={"email": "bob@example.com", "email_verified": False},
        )
        client = GoogleOAuthClient("client-id", "secret", transport)
        with pytest.raises(GoogleIdentityError, match="not verified"):
            await client.resolve_verified_email(code="c", redirect_uri="https://bff/callback")

    async def test_raises_on_token_endpoint_failure(self) -> None:
        transport = _FakeTransport(token_response={}, userinfo_response={})

        async def failing_post(url: str, data: dict[str, str]) -> _FakeResponse:
            return _FakeResponse({}, status_ok=False)

        transport.post = failing_post  # type: ignore[method-assign]
        client = GoogleOAuthClient("client-id", "secret", transport)
        with pytest.raises(GoogleIdentityError, match="token exchange failed"):
            await client.resolve_verified_email(code="c", redirect_uri="https://bff/callback")
