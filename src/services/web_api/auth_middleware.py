"""WebSessionMiddleware -- resolves the browser session cookie into a WebSession
attached to request.state, for every protected BFF route (ADR-005 Sec 3/4.1).

Deliberately separate from ``src.services.auth.middleware.AuthMiddleware``
(which resolves ``AuthContext`` from a Bearer/API-key/mTLS header for the
external-facing ``api_platform`` surface) -- this reads the browser's
``voiceos_session`` cookie and decodes it via ``WebSessionCodec``, never a
header, and never produces an ``AuthContext`` (ADR-005 Sec 3: PlatformActor
has no tenant_id, so it cannot flow through a type that requires one).

Per Next.js's own guidance (already noted in this project's ``proxy.ts``):
never rely on the edge-level cookie-presence check alone -- this middleware
is the real verification point every protected route depends on.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.services.platform_admin.rbac_engine import PlatformRBACEngine

from .session import WebSession, WebSessionCodec

_SESSION_COOKIE = "voiceos_session"


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code)


class WebSessionMiddleware:
    """Decodes ``voiceos_session`` into ``request.state.session`` for every request.

    Does not reject unauthenticated requests itself (some BFF routes, like
    the auth routes and /system/health, are intentionally public) -- route
    handlers or a narrower per-route guard (see ``require_platform_role``/
    ``require_tenant_actor`` below) enforce presence and role.
    """

    def __init__(self, app: ASGIApp, session_codec: WebSessionCodec) -> None:
        self.app = app
        self._codec = session_codec

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        token = request.cookies.get(_SESSION_COOKIE)
        session = self._codec.decode(token) if token else None
        scope.setdefault("state", {})
        scope["state"]["session"] = session

        await self.app(scope, receive, send)


def get_session(request: Request) -> WebSession | None:
    """Read the WebSession a prior WebSessionMiddleware pass resolved, if any."""
    return request.state.session  # type: ignore[no-any-return]


class SessionRequiredError(Exception):
    """Raised by route-guard helpers when no valid session is present (maps to 401)."""


class ForbiddenError(Exception):
    """Raised by route-guard helpers when the session lacks the required role/permission (maps to 403)."""


def require_platform_session(request: Request) -> WebSession:
    """Return the request's WebSession, requiring ``actor_kind == "platform"``.

    Raises:
        SessionRequiredError: No valid session cookie.
        ForbiddenError: A valid session exists but is a tenant actor, not platform.
    """
    session = get_session(request)
    if session is None:
        raise SessionRequiredError("no valid session")
    if session.actor_kind != "platform":
        raise ForbiddenError("platform actor required")
    return session


_RBAC_ENGINE = PlatformRBACEngine()


def require_platform_permission(request: Request, permission: str) -> WebSession:
    """Return the request's WebSession, requiring a platform actor holding ``permission``.

    Raises:
        SessionRequiredError: No valid session cookie.
        ForbiddenError: A platform session exists but lacks ``permission``
            (or the session is a tenant actor).
    """
    session = require_platform_session(request)
    if not _RBAC_ENGINE.check(session.role, permission):
        raise ForbiddenError(f"missing permission: {permission}")
    return session


def require_tenant_session(request: Request) -> WebSession:
    """Return the request's WebSession, requiring ``actor_kind == "tenant"``.

    Raises:
        SessionRequiredError: No valid session cookie.
        ForbiddenError: A valid session exists but is a platform actor, not tenant.
    """
    session = get_session(request)
    if session is None:
        raise SessionRequiredError("no valid session")
    if session.actor_kind != "tenant":
        raise ForbiddenError("tenant actor required")
    return session


def require_tenant_permission(request: Request, permission: str) -> WebSession:
    """Return the request's WebSession, requiring a tenant actor holding ``permission``.

    Checks ``session.permissions`` -- the assigned Role's actual ``permissions``
    DB column, baked into the session at login time (Law of Authority: the
    ``roles`` table row is authoritative, not a hardcoded role-name mirror).
    This does NOT use ``authz.RBACEngine``/``ROLE_PERMISSIONS`` -- that fixed
    dict is a convenient *default* for how tenants get provisioned (see
    ``tenant_management.provisioner.ADMIN_ROLE_PERMISSIONS``), not the
    enforcement source of truth once a role exists as a real DB row.

    Raises:
        SessionRequiredError: No valid session cookie.
        ForbiddenError: A tenant session exists but lacks ``permission``
            (or the session is a platform actor).
    """
    session = require_tenant_session(request)
    if permission not in session.permissions:
        raise ForbiddenError(f"missing permission: {permission}")
    return session


__all__ = [
    "ForbiddenError",
    "SessionRequiredError",
    "WebSessionMiddleware",
    "get_session",
    "require_platform_permission",
    "require_platform_session",
    "require_tenant_permission",
    "require_tenant_session",
]
