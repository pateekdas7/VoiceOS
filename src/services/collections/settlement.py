"""SettlementService — offer -> accept -> authorize -> disburse workflow (V5 Ch4.4).

``authorize`` is a metadata gate (``approved_by``/``authorized_at``, migration
0019) recorded against an ``ACCEPTED`` settlement, not a new
``SettlementStatus`` enum member — the frozen Sprint-002 contract only has
``PROPOSED/ACCEPTED/REJECTED/EXPIRED/PAID``. Settlements at or below
``APPROVAL_THRESHOLD_MINOR`` (V5 §5.13) may disburse without authorization;
settlements above it must be authorized first (Sprint-022 deviation — see
CHANGELOG.md).

Architecture: V5 Ch4.4 (Settlement Workflow); V4 Ch15 (human-in-the-loop).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from src.libs.audit.logger import AuditLogger
from src.libs.contracts.models.collections import Settlement, SettlementStatus
from src.libs.contracts.primitives import CustomerId, TenantId
from src.libs.event_bus.publisher import Publisher
from src.libs.repositories.settlement import SettlementRepository

APPROVAL_THRESHOLD_MINOR = 50_000
"""Settlements above this amount require human authorization before disbursal (V5 §5.13)."""


class SettlementTransitionError(ValueError):
    """An invalid settlement state transition was attempted."""


class SettlementService:
    """Offer -> accept -> authorize -> disburse settlement workflow (V5 Ch4.4)."""

    def __init__(
        self,
        repository: SettlementRepository,
        publisher: Publisher | None = None,
        audit_logger: AuditLogger | None = None,
        approval_threshold_minor: int = APPROVAL_THRESHOLD_MINOR,
    ) -> None:
        self._repo = repository
        self._publisher = publisher
        self._audit_logger = audit_logger
        self._approval_threshold_minor = approval_threshold_minor

    def offer(
        self,
        tenant_id: TenantId,
        customer_id: CustomerId,
        loan_account_id: str,
        settlement_amount_minor: int,
        waiver_amount_minor: int,
        currency: str,
        offer_expiry: datetime,
    ) -> Settlement:
        """Propose a settlement offer (status starts ``PROPOSED``)."""
        now = datetime.now(UTC)
        settlement = Settlement(
            settlement_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            customer_id=customer_id,
            loan_account_id=loan_account_id,
            settlement_amount_minor=settlement_amount_minor,
            waiver_amount_minor=waiver_amount_minor,
            currency=currency,
            offer_expiry=offer_expiry,
            status=SettlementStatus.PROPOSED,
            proposed_at=now,
            updated_at=now,
        )
        self._repo.create(settlement)
        self._publish(
            "saas.settlement.offered",
            settlement,
            {
                "customer_id": str(customer_id),
                "loan_account_id": loan_account_id,
                "settlement_id": settlement.settlement_id,
                "settlement_amount_minor": settlement_amount_minor,
                "waiver_amount_minor": waiver_amount_minor,
                "currency": currency,
            },
        )
        return settlement

    def accept(self, tenant_id: TenantId, settlement_id: str) -> Settlement:
        """PROPOSED -> ACCEPTED. Raises if the settlement isn't currently PROPOSED."""
        settlement = self._require(tenant_id, settlement_id)
        self._require_status(settlement, SettlementStatus.PROPOSED, "accept")
        self._repo.update_status(tenant_id, settlement_id, SettlementStatus.ACCEPTED)
        accepted = self._require(tenant_id, settlement_id)
        self._publish(
            "saas.settlement.accepted",
            accepted,
            {
                "customer_id": str(accepted.customer_id),
                "settlement_id": settlement_id,
                "accepted_at": datetime.now(UTC).isoformat(),
            },
        )
        return accepted

    def requires_authorization(self, settlement: Settlement) -> bool:
        """Whether this settlement's amount exceeds the human-approval threshold (V5 §5.13)."""
        return settlement.settlement_amount_minor > self._approval_threshold_minor

    def authorize(self, tenant_id: TenantId, settlement_id: str, approved_by: str) -> Settlement:
        """Record human authorization for an ACCEPTED, over-threshold settlement.

        Does not change ``status`` (stays ``ACCEPTED``) — see module docstring.
        """
        settlement = self._require(tenant_id, settlement_id)
        self._require_status(settlement, SettlementStatus.ACCEPTED, "authorize")
        authorized_at = datetime.now(UTC)
        self._repo.record_authorization(tenant_id, settlement_id, approved_by, authorized_at)
        if self._audit_logger is not None:
            self._audit_logger.record(
                tenant_id, approved_by, "SETTLEMENT_AUTHORIZED", "Settlement", settlement_id, "SUCCESS"
            )
        self._publish(
            "saas.settlement.authorized",
            settlement,
            {"settlement_id": settlement_id, "approved_by": approved_by},
        )
        return self._require(tenant_id, settlement_id)

    def disburse(self, tenant_id: TenantId, settlement_id: str, approved_by: str | None = None) -> Settlement:
        """ACCEPTED -> PAID. Over-threshold settlements must already be authorized.

        Args:
            approved_by: If provided and the settlement has not yet been
                authorized, authorizes it as part of this call (a single
                disbursement request may carry its own approver).
        """
        settlement = self._require(tenant_id, settlement_id)
        self._require_status(settlement, SettlementStatus.ACCEPTED, "disburse")
        if self.requires_authorization(settlement):
            already_authorized = self._repo.get_approved_by(tenant_id, settlement_id) is not None
            if not already_authorized:
                if approved_by is None:
                    raise SettlementTransitionError(
                        f"settlement {settlement_id} exceeds the approval threshold "
                        f"({self._approval_threshold_minor} minor units) and has not been authorized"
                    )
                self.authorize(tenant_id, settlement_id, approved_by)
        self._repo.update_status(tenant_id, settlement_id, SettlementStatus.PAID)
        paid = self._require(tenant_id, settlement_id)
        self._publish(
            "saas.settlement.disbursed",
            paid,
            {
                "customer_id": str(paid.customer_id),
                "settlement_id": settlement_id,
                "disbursed_at": datetime.now(UTC).isoformat(),
            },
        )
        return paid

    def reject(self, tenant_id: TenantId, settlement_id: str) -> Settlement:
        settlement = self._require(tenant_id, settlement_id)
        self._require_status(settlement, SettlementStatus.PROPOSED, "reject")
        self._repo.update_status(tenant_id, settlement_id, SettlementStatus.REJECTED)
        return self._require(tenant_id, settlement_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _require(self, tenant_id: TenantId, settlement_id: str) -> Settlement:
        settlement = self._repo.get(tenant_id, settlement_id)
        if settlement is None:
            raise ValueError(f"settlement not found: {settlement_id}")
        return settlement

    def _require_status(self, settlement: Settlement, expected: SettlementStatus, action: str) -> None:
        if settlement.status != expected:
            raise SettlementTransitionError(
                f"cannot {action} settlement {settlement.settlement_id}: status is "
                f"{settlement.status.value}, expected {expected.value}"
            )

    def _publish(self, event_type: str, settlement: Settlement, extra_payload: dict[str, object]) -> None:
        if self._publisher is None:
            return
        payload: dict[str, object] = {"tenant_id": str(settlement.tenant_id)}
        payload.update(extra_payload)
        self._publisher.publish(
            event_type=event_type,
            tenant_id=settlement.tenant_id,
            payload=payload,
            correlation_id=settlement.settlement_id,
        )
