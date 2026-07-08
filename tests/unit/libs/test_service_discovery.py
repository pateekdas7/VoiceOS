"""Unit tests for ServiceRegistry, ServiceResolver, and ServiceClient (V3 Ch11)."""

from __future__ import annotations

import pytest

from src.libs.circuit_breaker.breaker import CircuitBreaker, CircuitBreakerConfig, CircuitOpenError
from src.libs.service_discovery.client import ServiceClient
from src.libs.service_discovery.registry import ServiceEndpoint, ServiceRegistry
from src.libs.service_discovery.resolver import ServiceResolver


class TestServiceRegistry:
    def test_register_and_list(self) -> None:
        registry = ServiceRegistry()
        registry.register(ServiceEndpoint(name="llm", host="10.0.0.1", port=8000))

        endpoints = registry.list_endpoints("llm")

        assert len(endpoints) == 1
        assert endpoints[0].host == "10.0.0.1"

    def test_register_same_endpoint_twice_replaces_not_duplicates(self) -> None:
        registry = ServiceRegistry()
        registry.register(ServiceEndpoint(name="llm", host="10.0.0.1", port=8000, healthy=True))
        registry.register(ServiceEndpoint(name="llm", host="10.0.0.1", port=8000, healthy=False))

        endpoints = registry.list_endpoints("llm")

        assert len(endpoints) == 1
        assert endpoints[0].healthy is False

    def test_deregister_removes_endpoint(self) -> None:
        registry = ServiceRegistry()
        registry.register(ServiceEndpoint(name="llm", host="10.0.0.1", port=8000))

        registry.deregister("llm", "10.0.0.1", 8000)

        assert registry.list_endpoints("llm") == []

    def test_deregister_unknown_service_is_a_noop(self) -> None:
        registry = ServiceRegistry()
        registry.deregister("unknown", "10.0.0.1", 8000)  # must not raise

    def test_list_unknown_service_returns_empty(self) -> None:
        registry = ServiceRegistry()
        assert registry.list_endpoints("unknown") == []

    def test_list_healthy_filters_unhealthy_endpoints(self) -> None:
        registry = ServiceRegistry()
        registry.register(ServiceEndpoint(name="llm", host="10.0.0.1", port=8000, healthy=True))
        registry.register(ServiceEndpoint(name="llm", host="10.0.0.2", port=8000, healthy=False))

        healthy = registry.list_healthy("llm")

        assert len(healthy) == 1
        assert healthy[0].host == "10.0.0.1"


class TestServiceResolver:
    def test_falls_back_to_k8s_dns_convention_without_registry(self) -> None:
        resolver = ServiceResolver()

        url = resolver.resolve("llm-runtime", 8000)

        assert url == "http://llm-runtime.voiceos-runtime.svc.cluster.local:8000"

    def test_prefers_registered_healthy_endpoint(self) -> None:
        registry = ServiceRegistry()
        registry.register(ServiceEndpoint(name="llm-runtime", host="127.0.0.1", port=9000))
        resolver = ServiceResolver(registry)

        url = resolver.resolve("llm-runtime", 8000)

        assert url == "http://127.0.0.1:9000"

    def test_custom_namespace_and_domain(self) -> None:
        resolver = ServiceResolver(namespace="custom-ns", cluster_domain="example.internal")

        url = resolver.resolve("tts", 8200)

        assert url == "http://tts.custom-ns.example.internal:8200"


class TestServiceClient:
    async def test_call_resolves_and_invokes_operation(self) -> None:
        resolver = ServiceResolver(namespace="voiceos-runtime")
        breaker = CircuitBreaker("llm-runtime")
        client = ServiceClient("llm-runtime", resolver, breaker)

        seen_urls: list[str] = []

        async def operation(base_url: str) -> str:
            seen_urls.append(base_url)
            return "response"

        result = await client.call(8000, operation)

        assert result == "response"
        assert seen_urls == ["http://llm-runtime.voiceos-runtime.svc.cluster.local:8000"]

    async def test_retries_on_transient_failure_then_succeeds(self) -> None:
        resolver = ServiceResolver()
        breaker = CircuitBreaker("llm-runtime", CircuitBreakerConfig(failure_threshold=10))
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        client = ServiceClient("llm-runtime", resolver, breaker, max_retries=2, sleep=fake_sleep)

        attempts = 0

        async def flaky_operation(_base_url: str) -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise ConnectionError("transient")
            return "ok"

        result = await client.call(8000, flaky_operation)

        assert result == "ok"
        assert attempts == 2
        assert len(sleeps) == 1

    async def test_gives_up_after_max_retries(self) -> None:
        resolver = ServiceResolver()
        breaker = CircuitBreaker("llm-runtime", CircuitBreakerConfig(failure_threshold=10))

        async def always_sleep(_seconds: float) -> None:
            return None

        client = ServiceClient("llm-runtime", resolver, breaker, max_retries=1, sleep=always_sleep)

        async def always_fails(_base_url: str) -> str:
            raise ConnectionError("down")

        with pytest.raises(ConnectionError):
            await client.call(8000, always_fails)

    async def test_open_circuit_fails_fast_without_retrying(self) -> None:
        resolver = ServiceResolver()
        # A high failure_threshold keeps the breaker CLOSED through the first
        # call's own retries, so its failure is a plain ConnectionError; a
        # second call() then trips it OPEN via breaker.call() directly.
        breaker = CircuitBreaker("llm-runtime", CircuitBreakerConfig(failure_threshold=1))
        client = ServiceClient("llm-runtime", resolver, breaker, max_retries=3)

        async def always_fails(_base_url: str) -> str:
            raise ConnectionError("down")

        with pytest.raises((ConnectionError, CircuitOpenError)):
            await client.call(8000, always_fails)  # trips the breaker OPEN

        assert breaker.state.value == "open"

        attempts = 0

        async def should_not_be_called(_base_url: str) -> str:
            nonlocal attempts
            attempts += 1
            return "unreachable"

        with pytest.raises(CircuitOpenError):
            await client.call(8000, should_not_be_called)

        assert attempts == 0
