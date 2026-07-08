"""CallbackScheduler — customer callback scheduling (V5 Ch4.5).

Architecture: V5 Ch4.5 (Callback Scheduling); campaign hand-off is Sprint-023 scope.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from src.libs.contracts.models.collections import CallbackRequest
from src.libs.contracts.primitives import CallId, CustomerId, TenantId
from src.libs.event_bus.publisher import Publisher
from src.libs.repositories.callback import CallbackRepository

_CALLBACK_SCHEDULED_EVENT_TYPE = "saas.callback.scheduled"


class CallbackScheduler:
    """Schedules and tracks customer callback requests (V5 Ch4.5)."""

    def __init__(self, repository: CallbackRepository, publisher: Publisher | None = None) -> None:
        self._repo = repository
        self._publisher = publisher

    def schedule(
        self,
        tenant_id: TenantId,
        call_id: CallId,
        customer_id: CustomerId,
        loan_account_id: str,
        preferred_time: datetime,
        phone_number: str,
        timezone: str = "Asia/Kolkata",
    ) -> CallbackRequest:
        callback = CallbackRequest(
            callback_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            call_id=call_id,
            customer_id=customer_id,
            loan_account_id=loan_account_id,
            preferred_time=preferred_time,
            timezone=timezone,
            phone_number=phone_number,
            recorded_at=datetime.now(UTC),
        )
        self._repo.create(callback)
        if self._publisher is not None:
            self._publisher.publish(
                event_type=_CALLBACK_SCHEDULED_EVENT_TYPE,
                tenant_id=tenant_id,
                payload={
                    "call_id": str(call_id),
                    "customer_id": str(customer_id),
                    "loan_account_id": loan_account_id,
                    "callback_id": callback.callback_id,
                    "preferred_time": preferred_time.isoformat(),
                },
                correlation_id=str(call_id),
            )
        return callback

    def find_pending(self, tenant_id: TenantId, customer_id: CustomerId) -> tuple[CallbackRequest, ...]:
        return self._repo.find_pending(tenant_id, customer_id)

    def mark_fulfilled(self, tenant_id: TenantId, callback_id: str) -> None:
        self._repo.mark_fulfilled(tenant_id, callback_id)
