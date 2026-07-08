"""HumanOversightRouter — routes REQUIRE_HUMAN verdicts to the supervisor queue (V4 Ch15).

Architecture: V4 Ch15 (Human Oversight) §15.7 (``request_approval``), §15.9.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

REVIEW_REQUIRED_EVENT_TYPE = "human_oversight.review_required"


@dataclass(frozen=True)
class ReviewRequest:
    """One item routed to the human supervisor work queue (V4 Ch15 §15.6)."""

    call_id: str
    tenant_id: str
    reason: str


@runtime_checkable
class HumanOversightRouterPort(Protocol):
    """Structural port every REQUIRE_HUMAN sink must satisfy (V4 Ch15 §15.9).

    Both :class:`HumanOversightRouter` (in-memory, Sprint-020) and
    :class:`src.services.hitl.queue.HITLQueue` (durable, Sprint-023) satisfy
    this — ``GovernanceLayer``/``AIGovernanceService`` accept either without
    caring which, so wiring in the durable queue is a pure constructor-arg
    swap, no other code changes.
    """

    def route(self, call_id: str, tenant_id: str, reason: str) -> Any: ...


class HumanOversightRouter:
    """Routes REQUIRE_HUMAN verdicts into an in-process work queue (+ EventBus if wired)."""

    def __init__(self, publisher: Any | None = None) -> None:
        self._publisher = publisher
        self._queue: list[ReviewRequest] = []

    def route(self, call_id: str, tenant_id: str, reason: str) -> ReviewRequest:
        """Enqueue a review request for supervisor pickup (V4 Ch15 §15.9)."""
        request = ReviewRequest(call_id=call_id, tenant_id=tenant_id, reason=reason)
        self._queue.append(request)

        if self._publisher is not None:
            self._publisher.publish(
                event_type=REVIEW_REQUIRED_EVENT_TYPE,
                tenant_id=tenant_id,
                payload={"call_id": call_id, "reason": reason},
                correlation_id=call_id,
            )

        return request

    @property
    def queue(self) -> tuple[ReviewRequest, ...]:
        """The current supervisor work queue (oldest first)."""
        return tuple(self._queue)
