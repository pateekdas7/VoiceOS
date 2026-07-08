"""ServiceClient — resolver + circuit breaker + bounded retry for inter-service calls.

Combines :class:`ServiceResolver` (find the endpoint), :class:`CircuitBreaker`
(fail fast on a dependency that is already down), and a bounded
exponential-backoff retry (V3 Ch10 §10.12: "bounded attempts, exponential
backoff + jitter-free is acceptable here since retries are capped and the
breaker already protects against storms") into the one call path services
use to reach each other.

Architecture: V3 Ch11 (Service Discovery) §11.7 (ServiceClient composition);
V3 Ch14 (Circuit Breakers) §14.12 (retry budget).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from src.libs.circuit_breaker.breaker import CircuitBreaker, CircuitOpenError
from src.libs.service_discovery.resolver import ServiceResolver

T = TypeVar("T")

DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_BASE_SECONDS = 0.1


class ServiceClient:
    """Resolves, breaker-guards, and retries a call to another VoiceOS service."""

    def __init__(
        self,
        service_name: str,
        resolver: ServiceResolver,
        breaker: CircuitBreaker,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_base_seconds: float = DEFAULT_RETRY_BACKOFF_BASE_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """
        Args:
            service_name: The dependency's registry/DNS name.
            resolver: Resolves ``service_name`` to a base URL.
            breaker: The (per-service) circuit breaker guarding this dependency.
            max_retries: Maximum retry attempts after the first failure.
            retry_backoff_base_seconds: Base delay for exponential backoff
                (``base * 2**attempt``).
            sleep: Injectable sleep function, for deterministic tests.
        """
        self._service_name = service_name
        self._resolver = resolver
        self._breaker = breaker
        self._max_retries = max_retries
        self._retry_backoff_base_seconds = retry_backoff_base_seconds
        self._sleep = sleep

    async def call(self, port: int, operation: Callable[[str], Awaitable[T]]) -> T:
        """Resolve, then invoke ``operation(base_url)`` through the breaker with retry.

        Args:
            port: Port to resolve the service on.
            operation: Async callable taking the resolved base URL and
                performing the actual request.

        Returns:
            Whatever ``operation`` returns.

        Raises:
            CircuitOpenError: Immediately, without retrying — an open breaker
                means the dependency is already known to be down.
            Exception: The last failure, once ``max_retries`` is exhausted.
        """
        url = self._resolver.resolve(self._service_name, port)
        attempt = 0
        while True:
            try:
                return await self._breaker.call(operation, url)
            except CircuitOpenError:
                raise
            except Exception:
                if attempt >= self._max_retries:
                    raise
                await self._sleep(self._retry_backoff_base_seconds * (2**attempt))
                attempt += 1
