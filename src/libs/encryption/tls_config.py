"""TLSConfig — TLS 1.3 enforcement + cipher-suite pinning (V4 Ch8 §8.12 "In transit").

No VoiceOS service has a standalone HTTP/gRPC listener yet (every service
remains a library class until Sprint-026's K8s/Helm work — see
CPU_NODE_STATE.md §8.1, TT-006). ``TLSConfig`` is implemented and validated
in isolation here (builds a real ``ssl.SSLContext`` and asserts its
properties), the same treatment Sprint-018 gave mTLS ahead of any service
having a live network listener to bind it to.

Architecture: V4 Ch8 §8.12 ("TLS 1.2+/1.3 at the edge... strong cipher
suites pinned; no plaintext on untrusted networks"), §8.13 (config).
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass, field

# TLS 1.3's own cipher suites (RFC 8446 §B.4) — distinct from the legacy
# TLS 1.2 cipher-suite namespace and not configurable via set_ciphers();
# Python's ssl module always enables all three when minimum_version is
# TLSv1_3, so this list exists for documentation/assertion purposes.
TLS13_CIPHER_SUITES: tuple[str, ...] = (
    "TLS_AES_256_GCM_SHA384",
    "TLS_CHACHA20_POLY1305_SHA256",
    "TLS_AES_128_GCM_SHA256",
)


@dataclass(frozen=True)
class TLSConfig:
    """TLS 1.3-only configuration for a VoiceOS service's HTTPS/gRPC listener."""

    cert_path: str
    key_path: str
    ca_path: str | None = None
    require_client_cert: bool = False
    minimum_version: ssl.TLSVersion = field(default=ssl.TLSVersion.TLSv1_3)

    def build_ssl_context(self) -> ssl.SSLContext:
        """Build a real ``ssl.SSLContext`` enforcing TLS 1.3 for a server socket."""
        if self.require_client_cert and self.ca_path is None:
            raise ValueError("require_client_cert=True needs ca_path to verify client certificates")

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = self.minimum_version
        ctx.maximum_version = ssl.TLSVersion.TLSv1_3
        ctx.load_cert_chain(certfile=self.cert_path, keyfile=self.key_path)
        if self.require_client_cert:
            ctx.verify_mode = ssl.CERT_REQUIRED
            ctx.load_verify_locations(cafile=self.ca_path)
        return ctx

    def enforces_tls13_only(self, ctx: ssl.SSLContext) -> bool:
        """True if ``ctx`` cannot negotiate below TLS 1.3 (used by tests/health checks)."""
        return ctx.minimum_version == ssl.TLSVersion.TLSv1_3
