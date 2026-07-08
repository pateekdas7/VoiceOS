"""RecoveryManager — detects failure class, dispatches recovery (V3 Ch7).

Holds the failure-class -> strategy routing table, times and records every
recovery attempt to ``RecoveryLog``, and emits ``RecoveryStarted`` /
``RecoveryCompleted`` domain events (Sprint-013 EventBus). Strategy
signatures differ per failure class (see ``strategies/``), so dispatch is
split into ``strategy_for()`` (routing) and ``recover()`` (timing + logging +
event emission around the caller-supplied strategy invocation).

Architecture: V3 Ch7 (Crash Recovery — deterministic recovery per failure class).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from src.libs.contracts.primitives import CallId, TenantId
from src.libs.event_bus.publisher import Publisher
from src.libs.state.recovery_log import RecoveryAttempt, RecoveryLog

from .outcome import RecoveryOutcome

logger = logging.getLogger(__name__)

RECOVERY_STARTED_EVENT_TYPE = "reliability.recovery.started"
RECOVERY_COMPLETED_EVENT_TYPE = "reliability.recovery.completed"


class RecoveryManager:
    """Routes a failure class to its recovery strategy and audits the outcome."""

    def __init__(self, recovery_log: RecoveryLog, publisher: Publisher | None = None) -> None:
        self._recovery_log = recovery_log
        self._publisher = publisher
        self._strategies: dict[str, Any] = {}

    def register(self, failure_class: str, strategy: Any) -> None:
        """Register ``strategy`` as the handler for ``failure_class``."""
        self._strategies[failure_class] = strategy

    def strategy_for(self, failure_class: str) -> Any:
        """Return the registered strategy for ``failure_class``.

        Raises:
            ValueError: If no strategy is registered for this failure class.
        """
        try:
            return self._strategies[failure_class]
        except KeyError:
            raise ValueError(f"No recovery strategy registered for failure class {failure_class!r}") from None

    async def recover(
        self,
        tenant_id: TenantId,
        call_id: CallId,
        failure_class: str,
        recover_fn: Callable[[Any], Awaitable[RecoveryOutcome]],
    ) -> RecoveryOutcome:
        """Dispatch, time, and audit one recovery attempt.

        Args:
            tenant_id: Tenant scope (AR-8).
            call_id: The call being recovered.
            failure_class: Routing key into the strategy table (e.g.
                ``'cpu_restart'``).
            recover_fn: Callable that invokes the resolved strategy's
                ``recover(...)`` with whatever failure-class-specific
                arguments it needs, and returns the RecoveryOutcome.

        Returns:
            The RecoveryOutcome produced by ``recover_fn`` (or a failed one
            if ``recover_fn`` raised — the exception is logged, not
            propagated, so a broken strategy escalates via the audit log
            rather than crashing the caller).
        """
        strategy = self.strategy_for(failure_class)
        started_at = datetime.now(UTC)
        if self._publisher is not None:
            self._publisher.publish(
                event_type=RECOVERY_STARTED_EVENT_TYPE,
                tenant_id=tenant_id,
                payload={"call_id": call_id, "failure_class": failure_class},
                correlation_id=call_id,
            )

        try:
            outcome = await recover_fn(strategy)
        except Exception as exc:
            logger.exception("RecoveryManager: strategy %r failed for call %s", failure_class, call_id)
            outcome = RecoveryOutcome(success=False, detail={"error": str(exc)})

        completed_at = datetime.now(UTC)
        duration_ms = int((completed_at - started_at).total_seconds() * 1000)

        self._recovery_log.record(
            tenant_id,
            RecoveryAttempt(
                call_id=call_id,
                failure_class=failure_class,
                strategy_name=getattr(strategy, "strategy_name", failure_class),
                outcome="success" if outcome.success else "failure",
                detail=outcome.detail,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
            ),
        )

        if self._publisher is not None and outcome.success:
            events_replayed = outcome.detail.get("events_replayed", 0)
            self._publisher.publish(
                event_type=RECOVERY_COMPLETED_EVENT_TYPE,
                tenant_id=tenant_id,
                payload={
                    "call_id": call_id,
                    "duration_ms": duration_ms,
                    "events_replayed": events_replayed,
                },
                correlation_id=call_id,
            )

        if not outcome.success:
            logger.critical(
                "RecoveryManager: recovery FAILED for call %s (failure_class=%s) — escalating to CRITICAL alert",
                call_id,
                failure_class,
            )

        return outcome
