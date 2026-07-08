"""ServiceResolver — resolves a service name to a callable base URL (V3 Ch11).

Prefers a locally registered endpoint (test doubles / explicit overrides);
falls back to the Kubernetes in-cluster DNS naming convention
(``<service>.<namespace>.svc.cluster.local``), which is how VoiceOS
services will address each other once Sprint-026 wires up K8s Services.

Architecture: V3 Ch11 (Service Discovery) §11.6 ("route by role/capability");
V3 Ch2 (Distributed System Architecture) — namespace topology.
"""

from __future__ import annotations

from src.libs.service_discovery.registry import ServiceRegistry

DEFAULT_NAMESPACE = "voiceos-runtime"
DEFAULT_CLUSTER_DOMAIN = "svc.cluster.local"


class ServiceResolver:
    """Resolves a service name (+ port) to an ``http://`` base URL."""

    def __init__(
        self,
        registry: ServiceRegistry | None = None,
        *,
        namespace: str = DEFAULT_NAMESPACE,
        cluster_domain: str = DEFAULT_CLUSTER_DOMAIN,
    ) -> None:
        """
        Args:
            registry: An optional ServiceRegistry consulted first — lets tests
                and local development override DNS-based resolution.
            namespace: Kubernetes namespace the target services run in.
            cluster_domain: Cluster-internal DNS domain suffix.
        """
        self._registry = registry
        self._namespace = namespace
        self._cluster_domain = cluster_domain

    def resolve(self, service_name: str, port: int) -> str:
        """Return the base URL to reach ``service_name`` on ``port``.

        Uses a healthy registered endpoint if one exists; otherwise falls
        back to the Kubernetes DNS convention
        ``http://<service_name>.<namespace>.<cluster_domain>:<port>``.
        """
        if self._registry is not None:
            healthy = self._registry.list_healthy(service_name)
            if healthy:
                endpoint = healthy[0]
                return f"http://{endpoint.host}:{endpoint.port}"

        return f"http://{service_name}.{self._namespace}.{self._cluster_domain}:{port}"
