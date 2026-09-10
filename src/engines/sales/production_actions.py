"""SalesProductionActionDispatcher — Phase 3 Production Action Layer.

Dispatches real-world production actions derived from the SalesState after
each turn (post RI-4 DecisionEnvelope commit). All operations are best-effort
and logged on failure — they NEVER abort turn processing.

Architecture: VoiceOS Phase 3 Production Action Layer.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from src.libs.contracts.primitives import CallId, CustomerId, TenantId

from .schema import SalesAction

if TYPE_CHECKING:
    from src.libs.contracts.context import CustomerContext
    from src.services.collections.callback import CallbackScheduler

logger = logging.getLogger(__name__)


class SalesProductionActionDispatcher:
    """Dispatches production actions from SalesState after each turn.

    Runs AFTER DecisionEnvelope is committed (RI-4). All operations are
    best-effort — failures are logged but never abort turn processing.

    Architecture: Phase 3 production wiring.

    Safety rules:
    - SCHEDULE_FOLLOWUP: only if requested_callback_time is not None
      AND no existing pending callback exists for this call (dedup check).
    - HUMAN_HANDOFF: returns SalesAction.HUMAN_HANDOFF to caller — caller
      must invoke escalate_call(). The dispatcher does NOT call
      escalate_call() directly (it doesn't hold the engine handle).
    - All others: no-op (LLM/TTS handles via prompt directive).
    """

    def __init__(
        self,
        callback_scheduler: "CallbackScheduler | None" = None,
    ) -> None:
        self._callback_scheduler = callback_scheduler
        # Per-instance dedup set: tracks call_ids for which we've already
        # scheduled a callback this session. Prevents double-scheduling
        # when the customer repeats the CALLBACK intent across multiple turns.
        self._scheduled_call_ids: set[str] = set()

    def dispatch(
        self,
        sales_state: dict | None,
        context: "CustomerContext | None",
        tenant_id: str,
        call_id: str,
    ) -> SalesAction | None:
        """Inspect sales_state['next_action'] and dispatch the production action.

        Returns the action that was dispatched (for logging/handoff signalling),
        or None when no production action was needed.

        Args:
            sales_state: Serialized SalesState dict from ResponsePlan.sales_state.
            context: Authoritative CustomerContext for the call.
            tenant_id: Tenant scope string.
            call_id: Active call session identifier.

        Returns:
            SalesAction dispatched, or None. Callers must check for
            SalesAction.HUMAN_HANDOFF and invoke escalate_call() themselves.
        """
        if not sales_state:
            return None

        raw_action = sales_state.get("next_action", "")
        if not raw_action:
            return None

        try:
            action = SalesAction(raw_action)
        except ValueError:
            logger.debug(
                "SalesProductionActionDispatcher: unknown next_action=%r for call %s — skipping",
                raw_action,
                call_id,
            )
            return None

        if action == SalesAction.SCHEDULE_FOLLOWUP:
            return self._handle_schedule_followup(sales_state, context, tenant_id, call_id)

        if action == SalesAction.HUMAN_HANDOFF:
            logger.info(
                "SalesProductionActionDispatcher: HUMAN_HANDOFF requested for call %s — "
                "signalling caller to invoke escalate_call()",
                call_id,
            )
            return SalesAction.HUMAN_HANDOFF

        # All other actions: LLM/TTS handles via prompt directive. No production action.
        return None

    # ------------------------------------------------------------------
    # Private handlers
    # ------------------------------------------------------------------

    def _handle_schedule_followup(
        self,
        sales_state: dict,
        context: "CustomerContext | None",
        tenant_id: str,
        call_id: str,
    ) -> SalesAction | None:
        """Schedule a callback if conditions are met and not already done."""
        if self._callback_scheduler is None:
            logger.debug(
                "SalesProductionActionDispatcher: SCHEDULE_FOLLOWUP but no "
                "callback_scheduler wired — skipping for call %s",
                call_id,
            )
            return None

        # Safety: must have an explicit requested_callback_time from the customer.
        raw_time = sales_state.get("requested_callback_time")
        if not raw_time:
            logger.debug(
                "SalesProductionActionDispatcher: SCHEDULE_FOLLOWUP but "
                "requested_callback_time is None — callback not scheduled for call %s",
                call_id,
            )
            return None

        try:
            preferred_time = datetime.fromisoformat(raw_time)
        except (ValueError, TypeError):
            logger.warning(
                "SalesProductionActionDispatcher: could not parse "
                "requested_callback_time=%r for call %s — skipping",
                raw_time,
                call_id,
            )
            return None

        # Dedup: schedule at most once per call_id.
        if call_id in self._scheduled_call_ids:
            logger.debug(
                "SalesProductionActionDispatcher: callback already scheduled "
                "for call %s — skipping duplicate",
                call_id,
            )
            return SalesAction.SCHEDULE_FOLLOWUP  # already done; signal as if done

        # Extract customer and loan details from context.
        customer_id_str = ""
        loan_account_id = ""
        phone_number = ""
        if context is not None:
            customer_id_str = str(context.customer_id)
            phone_number = str(context.primary_party.contact.phone_number)
            if context.loans:
                loan_account_id = str(context.loans[0].account_id)

        try:
            self._callback_scheduler.schedule(
                tenant_id=TenantId(tenant_id),
                call_id=CallId(call_id),
                customer_id=CustomerId(customer_id_str) if customer_id_str else CustomerId("unknown"),
                loan_account_id=loan_account_id,
                preferred_time=preferred_time,
                phone_number=phone_number,
                timezone="Asia/Kolkata",
            )
            self._scheduled_call_ids.add(call_id)
            logger.info(
                "SalesProductionActionDispatcher: callback scheduled for call %s "
                "customer %s at %s — NOT VERIFIED (no dialer drains it yet)",
                call_id,
                customer_id_str,
                preferred_time.isoformat(),
            )
            return SalesAction.SCHEDULE_FOLLOWUP
        except Exception:
            logger.exception(
                "SalesProductionActionDispatcher: CallbackScheduler.schedule() failed "
                "for call %s — continuing without callback",
                call_id,
            )
            return None
