"""Domain events for the reliability infrastructure.

Covers state snapshots, crash recovery, idempotency key lifecycle, and
circuit breaker state transitions. Consumed by the OperationalDashboard,
AlertRouter, and the Replay/Recovery subsystems.

Architecture: V3 Ch4 (Snapshots), V3 Ch7 (Replay), V3 Ch8 (Idempotency),
              V3 Ch9 (Circuit Breaker), V3 Ch10 (Recovery);
              V3 Ch3 (Event Bus); V6 Ch6 EV-1.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from ..primitives import CallId
from .envelope import DomainEvent


class SnapshotCreated(DomainEvent):
    """Emitted when a state snapshot is written to Redis (V3 Ch4).

    Used by the RecoveryManager to identify the latest restorable checkpoint
    for a call. ``snapshot_key`` is the Redis key holding the serialized
    call-state blob.
    """

    event_type: Literal["reliability.snapshot.created"] = "reliability.snapshot.created"
    call_id: CallId
    snapshot_key: str
    """Redis key holding the snapshot (e.g. 'snap:{tenant_id}:{call_id}:v{version}')."""
    snapshot_version: int = Field(ge=1)
    """Monotone version counter. Consumers always restore the highest version."""
    size_bytes: int = Field(ge=0)
    """Serialized snapshot size for capacity monitoring."""


class RecoveryStarted(DomainEvent):
    """Emitted when the RecoveryManager begins restoring a crashed call (V3 Ch10).

    Triggered by a process restart or health-check failure. The recovery
    process replays events from the event log to rebuild the call state.
    """

    event_type: Literal["reliability.recovery.started"] = "reliability.recovery.started"
    call_id: CallId
    last_snapshot_version: int = Field(ge=0)
    """Version of the most recent valid snapshot found. 0 means no snapshot."""
    triggered_by: str
    """Recovery trigger: 'process_restart' | 'health_check_failure' | 'manual'."""
    events_to_replay: int = Field(ge=0)
    """Number of events in the event log to replay after the snapshot."""


class RecoveryCompleted(DomainEvent):
    """Emitted when recovery succeeds and the call is back in a consistent state.

    Architecture: V3 Ch10.
    """

    event_type: Literal["reliability.recovery.completed"] = "reliability.recovery.completed"
    call_id: CallId
    recovered_to_version: int = Field(ge=0)
    """Event log position after successful recovery."""
    duration_ms: int = Field(ge=0)
    """Total recovery duration in milliseconds."""
    events_replayed: int = Field(ge=0)
    """Number of events actually replayed from the event log."""


class IdempotencyKeyCreated(DomainEvent):
    """Emitted when an idempotency key is stored for an authoritative action (V3 Ch8).

    Invariant EV-7 requires that every event with an external effect carries
    an idempotency key. This event lets the dedup layer verify coverage.
    """

    event_type: Literal["reliability.idempotency.key_created"] = "reliability.idempotency.key_created"
    key: str
    """The idempotency key string (typically a UUID derived from the request ID)."""
    resource_type: str
    """The type of resource being protected: 'ptp' | 'sms' | 'payment' | etc."""
    expires_at: datetime
    """UTC expiry timestamp. Keys auto-expire to limit storage growth."""


class CircuitBreakerOpened(DomainEvent):
    """Emitted when a circuit breaker trips to OPEN state (V3 Ch9).

    The circuit is tripped when the failure rate exceeds the configured
    threshold. Downstream callers will receive fast failures until the
    circuit transitions to HALF_OPEN.
    """

    event_type: Literal["reliability.circuit_breaker.opened"] = "reliability.circuit_breaker.opened"
    service_name: str
    """Name of the protected service (e.g. 'llm-adapter', 'crm-service')."""
    failure_count: int = Field(ge=0)
    """Consecutive failures that triggered the trip."""
    threshold: int = Field(ge=1)
    """Configured failure threshold."""
    window_ms: int = Field(ge=0)
    """Rolling window duration in milliseconds."""


class CircuitBreakerClosed(DomainEvent):
    """Emitted when a circuit breaker resets to CLOSED state after recovery (V3 Ch9).

    Transition: HALF_OPEN → CLOSED after a successful probe request confirms
    the service has recovered.
    """

    event_type: Literal["reliability.circuit_breaker.closed"] = "reliability.circuit_breaker.closed"
    service_name: str
    recovered_after_ms: int = Field(ge=0)
    """Duration the circuit was in OPEN/HALF_OPEN state in milliseconds."""
    probe_success_count: int = Field(ge=1)
    """Number of successful probe requests that confirmed recovery."""


class GPUFailoverStarted(DomainEvent):
    """Emitted when GPU failover begins — a device has failed and sessions are draining.

    Consumers: OperationalDashboard, AlertRouter.
    Architecture: V1 Ch7 (GPU Scheduler GPU-2 graceful failover); V7 Ch6.
    """

    event_type: Literal["reliability.gpu.failover_started"] = "reliability.gpu.failover_started"
    failed_device_id: str
    """The GPU device ID that has failed (e.g. 'gpu-0')."""
    surviving_device_ids: list[str]
    """Device IDs that will absorb the drained sessions."""
    active_allocations_drained: int = Field(ge=0)
    """Number of VRAM allocations forcibly released from the failed device."""


class GPUFailoverCompleted(DomainEvent):
    """Emitted when GPU failover completes and surviving GPUs have absorbed the load.

    Architecture: V1 Ch7 (GPU Scheduler GPU-2 graceful failover); V7 Ch6.
    """

    event_type: Literal["reliability.gpu.failover_completed"] = "reliability.gpu.failover_completed"
    failed_device_id: str
    """The GPU device ID that failed."""
    surviving_device_ids: list[str]
    """Device IDs that absorbed the sessions."""
    duration_ms: int = Field(ge=0)
    """Total failover duration in milliseconds."""


__all__ = [
    "CircuitBreakerClosed",
    "CircuitBreakerOpened",
    "GPUFailoverCompleted",
    "GPUFailoverStarted",
    "IdempotencyKeyCreated",
    "RecoveryCompleted",
    "RecoveryStarted",
    "SnapshotCreated",
]
