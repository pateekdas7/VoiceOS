"""Service discovery — registry, DNS resolution, and a resilient inter-service client (V3 Ch11).

Architecture: V3 Ch11 (Service Discovery).
"""

from __future__ import annotations

from src.libs.service_discovery.client import ServiceClient
from src.libs.service_discovery.registry import ServiceEndpoint, ServiceRegistry
from src.libs.service_discovery.resolver import ServiceResolver

__all__ = [
    "ServiceClient",
    "ServiceEndpoint",
    "ServiceRegistry",
    "ServiceResolver",
]
