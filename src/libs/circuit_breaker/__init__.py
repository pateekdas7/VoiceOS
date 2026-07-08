"""Circuit breakers — fail-fast protection for external/cross-service calls (V3 Ch14).

Architecture: V3 Ch14 (Circuit Breakers).
"""

from __future__ import annotations

from src.libs.circuit_breaker.breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitOpenError,
    CircuitState,
)

__all__ = [
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitBreakerRegistry",
    "CircuitOpenError",
    "CircuitState",
]
