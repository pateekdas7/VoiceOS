"""AuthMiddleware — ASGI middleware enforcing authentication on every request
(V4 Ch5 §5.7). No endpoint is unauthenticated.

Detects Bearer JWT / ``X-API-Key`` / mTLS client certificate (forwarded by
the TLS-terminating proxy as ``X-Client-Cert``, the standard mTLS-behind-a-
proxy convention), resolves an :class:`AuthContext` via :class:`AuthService`,
and populates ``request.state.auth_context``. Returns 401 on any
authentication failure before the wrapped application ever runs.

Architecture: V4 Ch5 (Authentication); Sprint-018 AC ("no endpoint unauthenticated").
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .models import AuthenticationError
from .service import AuthService

CLIENT_CERT_HEADER = "x-client-cert"
"""Header the mTLS-terminating proxy forwards the verified client cert PEM under."""


class AuthMiddleware:
    """Pure-ASGI middleware wrapping every incoming HTTP request.

    Args:
        app: The wrapped ASGI application.
        auth_service: Resolves credentials to an :class:`AuthContext`.
    """

    def __init__(self, app: ASGIApp, auth_service: AuthService) -> None:
        self.app = app
        self._auth_service = auth_service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        try:
            auth_context = self._auth_service.authenticate(
                authorization_header=request.headers.get("authorization"),
                api_key_header=request.headers.get("x-api-key"),
                client_cert_pem=request.headers.get(CLIENT_CERT_HEADER),
            )
        except AuthenticationError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=401)
            await response(scope, receive, send)
            return

        scope.setdefault("state", {})
        scope["state"]["auth_context"] = auth_context
        await self.app(scope, receive, send)
