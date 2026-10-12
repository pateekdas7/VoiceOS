"""Unit tests for WebSessionMiddleware and the require_*_session guards (ADR-005 Sec 3/4.1)."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.services.web_api.auth_middleware import (
    ForbiddenError,
    SessionRequiredError,
    WebSessionMiddleware,
    get_session,
    require_platform_session,
    require_tenant_session,
)
from src.services.web_api.session import WebSessionCodec


@pytest.fixture
def codec() -> WebSessionCodec:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return WebSessionCodec(private_key, private_key.public_key())


def _build_app(codec: WebSessionCodec) -> Starlette:
    async def whoami(request: Request) -> JSONResponse:
        session = get_session(request)
        if session is None:
            return JSONResponse({"anonymous": True})
        return JSONResponse({"actor_kind": session.actor_kind, "email": session.email})

    async def admin_only(request: Request) -> JSONResponse:
        try:
            session = require_platform_session(request)
        except SessionRequiredError:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        except ForbiddenError:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return JSONResponse({"ok": True, "role": session.role})

    async def tenant_only(request: Request) -> JSONResponse:
        try:
            session = require_tenant_session(request)
        except SessionRequiredError:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        except ForbiddenError:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return JSONResponse({"ok": True, "tenant_id": session.tenant_id})

    app = Starlette(
        routes=[
            Route("/whoami", whoami),
            Route("/admin-only", admin_only),
            Route("/tenant-only", tenant_only),
        ]
    )
    return WebSessionMiddleware(app, session_codec=codec)  # type: ignore[return-value]


class TestWebSessionMiddleware:
    def test_no_cookie_resolves_to_no_session(self, codec: WebSessionCodec) -> None:
        client = TestClient(_build_app(codec))
        response = client.get("/whoami")
        assert response.json() == {"anonymous": True}

    def test_valid_cookie_resolves_to_session(self, codec: WebSessionCodec) -> None:
        token = codec.encode(
            actor_kind="platform", subject="pu-1", role="PLATFORM_ADMIN", email="a@voiceos.ai", tenant_id=None
        )
        client = TestClient(_build_app(codec))
        response = client.get("/whoami", cookies={"voiceos_session": token})
        assert response.json() == {"actor_kind": "platform", "email": "a@voiceos.ai"}

    def test_garbage_cookie_resolves_to_no_session(self, codec: WebSessionCodec) -> None:
        client = TestClient(_build_app(codec))
        response = client.get("/whoami", cookies={"voiceos_session": "not-a-real-token"})
        assert response.json() == {"anonymous": True}


class TestRequirePlatformSession:
    def test_no_session_returns_401(self, codec: WebSessionCodec) -> None:
        client = TestClient(_build_app(codec))
        response = client.get("/admin-only")
        assert response.status_code == 401

    def test_tenant_session_returns_403(self, codec: WebSessionCodec) -> None:
        token = codec.encode(actor_kind="tenant", subject="u-1", role="ADMIN", email="b@x.com", tenant_id="t-1")
        client = TestClient(_build_app(codec))
        response = client.get("/admin-only", cookies={"voiceos_session": token})
        assert response.status_code == 403

    def test_platform_session_succeeds(self, codec: WebSessionCodec) -> None:
        token = codec.encode(
            actor_kind="platform", subject="pu-1", role="PLATFORM_ADMIN", email="a@voiceos.ai", tenant_id=None
        )
        client = TestClient(_build_app(codec))
        response = client.get("/admin-only", cookies={"voiceos_session": token})
        assert response.status_code == 200
        assert response.json() == {"ok": True, "role": "PLATFORM_ADMIN"}


class TestRequireTenantSession:
    def test_no_session_returns_401(self, codec: WebSessionCodec) -> None:
        client = TestClient(_build_app(codec))
        response = client.get("/tenant-only")
        assert response.status_code == 401

    def test_platform_session_returns_403(self, codec: WebSessionCodec) -> None:
        token = codec.encode(
            actor_kind="platform", subject="pu-1", role="PLATFORM_ADMIN", email="a@voiceos.ai", tenant_id=None
        )
        client = TestClient(_build_app(codec))
        response = client.get("/tenant-only", cookies={"voiceos_session": token})
        assert response.status_code == 403

    def test_tenant_session_succeeds(self, codec: WebSessionCodec) -> None:
        token = codec.encode(actor_kind="tenant", subject="u-1", role="ADMIN", email="b@x.com", tenant_id="t-1")
        client = TestClient(_build_app(codec))
        response = client.get("/tenant-only", cookies={"voiceos_session": token})
        assert response.status_code == 200
        assert response.json() == {"ok": True, "tenant_id": "t-1"}
