"""Collections — loan account, EMI/DPD, PTP, settlement, callback, escalation (V5 Ch4).

The collections system of record: everything CustomerContextAssembler (CRM,
Sprint-022) reads to build the authoritative snapshot the conversation engine
uses for a call, plus the write-side workflows (PTP, settlement, callback,
escalation) a call can trigger.

Architecture: V5 Ch4 (Loan & Collections Management); Invariant RI-5.
"""

from __future__ import annotations

from .callback import CallbackScheduler
from .emi_schedule import EMIScheduleService
from .escalation import EscalationWorkflow
from .loan_account import LoanAccountService
from .promise_to_pay import PromiseToPayService, PTPValidationError
from .settlement import SettlementService, SettlementTransitionError

__all__ = [
    "CallbackScheduler",
    "EMIScheduleService",
    "EscalationWorkflow",
    "LoanAccountService",
    "PTPValidationError",
    "PromiseToPayService",
    "SettlementService",
    "SettlementTransitionError",
]
