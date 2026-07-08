"""CORSPolicy — strict origin allowlist, no wildcard (V4 Ch12 §12.2).

Architecture: V4 Ch12 (API Security) §12.2 ("no unauthenticated/unvalidated
entry"); strict allowlist mirrors the schema-validation stance in §12.12
("allow-list, not block-list").
"""

from __future__ import annotations


class CORSPolicy:
    """A strict CORS origin allowlist. Wildcard (``*``) origins are refused at construction."""

    def __init__(self, allowed_origins: frozenset[str]) -> None:
        if "*" in allowed_origins:
            raise ValueError("CORSPolicy forbids the wildcard origin '*' (V4 Ch12 strict allowlist)")
        self._allowed_origins = allowed_origins

    def is_allowed(self, origin: str) -> bool:
        return origin in self._allowed_origins

    def headers_for(self, origin: str) -> dict[str, str]:
        """CORS response headers for ``origin``, or ``{}`` if it isn't allowlisted."""
        if not self.is_allowed(origin):
            return {}
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
