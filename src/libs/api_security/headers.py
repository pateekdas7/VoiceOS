"""SecurityHeaders — mandatory response headers on every API response (V4 Ch12).

Architecture: V4 Ch12 (API Security). Applied via Sprint-020 Phase 2 as
middleware on every FastAPI service.
"""

from __future__ import annotations

from collections.abc import MutableMapping

DEFAULT_SECURITY_HEADERS: dict[str, str] = {
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
}


class SecurityHeaders:
    """Applies the mandatory security header set to outgoing API responses."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self._headers = dict(headers) if headers is not None else dict(DEFAULT_SECURITY_HEADERS)

    def apply(self, headers: MutableMapping[str, str]) -> None:
        """Merge the security headers into an existing response header mapping."""
        headers.update(self._headers)

    def as_dict(self) -> dict[str, str]:
        """The security header set as a plain dict (e.g. for test assertions)."""
        return dict(self._headers)
