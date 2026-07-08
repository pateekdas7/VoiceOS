"""ServiceRegistry — registers service endpoints on startup (V3 Ch11).

A lightweight, in-process registry (V3 Ch11 §11.6's ``ServiceInstance``/
``Registry``, scoped to what this sprint needs: registration and lookup —
lease/heartbeat expiry lands with the Sprint-026 K8s/Helm rollout, when
services become long-running processes rather than library classes).

Architecture: V3 Ch11 (Service Discovery) §11.6, §11.9.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceEndpoint:
    """A single registered instance of a service."""

    name: str
    host: str
    port: int
    healthy: bool = True


class ServiceRegistry:
    """In-memory registry of service endpoints, keyed by service name."""

    def __init__(self) -> None:
        self._endpoints: dict[str, list[ServiceEndpoint]] = {}

    def register(self, endpoint: ServiceEndpoint) -> None:
        """Register ``endpoint`` under its service name (idempotent — re-registering
        the same host:port replaces the existing entry rather than duplicating it)."""
        existing = self._endpoints.setdefault(endpoint.name, [])
        self._endpoints[endpoint.name] = [e for e in existing if (e.host, e.port) != (endpoint.host, endpoint.port)] + [
            endpoint
        ]

    def deregister(self, name: str, host: str, port: int) -> None:
        """Remove a previously registered endpoint. No-op if not present."""
        if name not in self._endpoints:
            return
        self._endpoints[name] = [e for e in self._endpoints[name] if (e.host, e.port) != (host, port)]

    def list_endpoints(self, name: str) -> list[ServiceEndpoint]:
        """Return every registered endpoint for ``name`` (empty list if none)."""
        return list(self._endpoints.get(name, []))

    def list_healthy(self, name: str) -> list[ServiceEndpoint]:
        """Return only the healthy registered endpoints for ``name``."""
        return [e for e in self.list_endpoints(name) if e.healthy]
