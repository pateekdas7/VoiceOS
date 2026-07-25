"""DialogueResponseEngine — deterministic scripted-response FSM for the
golden path (Path-A Phase 6f).

Ported from evaluation/founder-validation/conv_server.py's v3.12-v3.14
state machine (_run_state_machine/_classify_bucket/SCRIPT_TEMPLATES), which
drove Call-001. Redesigned (per the approved consolidation plan) to consume
real engine outputs instead of conv_server.py's own parallel parser:

  - Intent routing reads ResponsePlan.intents (IntentEngine, real).
  - Date/amount facts read ResponsePlan.entities (EntityExtractor, real,
    enriched in Phase 6a specifically for this consumer).
  - Negotiation bounds/finalized-commitment status come from
    ResponsePlan.negotiation_envelope (NegotiationEngine, real, Phase 1/5).
  - Per-call FSM state and the commitment ledger live on
    ConversationSessionState (Phase 6b) — this engine is the only writer of
    that ledger; it is never populated by a second regex parser.
  - Register/name/tone cleanup runs through RegisterGuard (Phase 6c).
  - The acknowledgment/prosody layer runs through EmpathyDirectiveComposer
    (Phase 6d).
  - Greeting/hangup/persona text comes from kavya_persona (Phase 6e).

This engine does NOT persist commitments — Phase 5's
ConversationEngine._persist_finalized_commitment() already does that from
ResponsePlan.negotiation_envelope.is_finalized_commitment, driven by the
real NegotiationEngine's decision, not by this engine's bucket routing.
This engine only decides what Kavya says.

Architecture: V2 Ch13 (dialogue state); V1 Ch12 (prompt/response assembly);
RI-5 (Law of Authority — every fact spoken comes from CustomerContext/
ResponsePlan.facts, never a literal).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.engines.dialogue_response.buckets import Bucket, classify_bucket
from src.engines.dialogue_response.installment import InstallmentPlan, InstallmentPlanKind, compute_installment_plan
from src.engines.dialogue_response.session_protocol import DialogueSessionState
from src.engines.dialogue_response.templates import SCRIPT_TEMPLATES, date_is_already_terminated, format_rupees
from src.engines.empathy_directive.directive import EmpathyDirective
from src.engines.empathy_directive.engine import EmpathyDirectiveComposer
from src.engines.prompt_builder.kavya_persona import AGENT_NAME, HANGUP_TEXT
from src.libs.ai_safety.register_guard import RegisterGuard, dedupe_name, sanitize_reply, strip_trailing_sir
from src.libs.contracts.context import CustomerContext
from src.libs.contracts.response_plan import ResponsePlan

logger = logging.getLogger(__name__)

_IDENTITY_YES_TOKENS = (
    "हाँ", "हां", "haan", "yes", "बोल रहा", "bol raha", "speaking",
    "हां जी", "haan ji", "jee", "जी", "जी हाँ", "ji haan",
    "बोलिए", "boliye", "haan boliye", "हाँ बोलिए", "yes speaking", "yeah speaking",
)  # fmt: skip
_IDENTITY_NO_TOKENS = (
    "wrong number", "गलत नंबर", "galat number", "नहीं", "nahi", "nahin",
    "no", "not me", "मैं नहीं", "main nahi", "wrong person",
)  # fmt: skip

_MONTHLY_CUE = ("महीने का", "प्रति महीना", "monthly", "per month", "हर महीने", "हर month", "एक महीने में")
_LUMPSUM_CUE = ("एक बार में", "एक साथ", "एक शॉट में", "one shot", "one-shot", "पूरा", "फुल", "full")

_ENGLISH_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _classify_identity_response(text: str) -> str:
    """Return 'confirm' / 'deny' / 'ambiguous' for the AWAIT_IDENTITY turn."""
    if not text:
        return "ambiguous"
    lower = text.lower()
    if any(tok in text or tok.lower() in lower for tok in _IDENTITY_NO_TOKENS):
        return "deny"
    if any(tok in text or tok.lower() in lower for tok in _IDENTITY_YES_TOKENS):
        return "confirm"
    return "ambiguous"


def _detect_cadence(user_text: str) -> str | None:
    lower = user_text.lower()
    if any(cue in user_text or cue in lower for cue in _MONTHLY_CUE):
        return "monthly"
    if any(cue in user_text or cue in lower for cue in _LUMPSUM_CUE):
        return "one-shot"
    return None


def _format_date_for_speech(iso_date: str) -> str:
    """Render a resolved ISO date (YYYY-MM-DD) as spoken Hinglish ('9 August').

    Speaking the resolved date rather than echoing the customer's raw phrase
    verbatim avoids parroting back a garbled ASR transcript — the date has
    already been resolved to a real calendar date by EntityExtractor.
    """
    try:
        year, month, day = (int(p) for p in iso_date.split("-"))
        return f"{day} {_ENGLISH_MONTHS[month - 1]}"
    except (ValueError, IndexError):
        return iso_date


def _resolve_outstanding_minor(context: CustomerContext | None, response_plan: ResponsePlan) -> int:
    if context is not None and context.primary_loan is not None:
        return context.primary_loan.outstanding_balance.amount_minor
    fact = response_plan.facts.get("outstanding_balance")
    if isinstance(fact, int):
        return fact
    logger.warning("DialogueResponseEngine: no outstanding balance available on context or facts")
    return 0


@dataclass(frozen=True)
class DialogueTurnOutput:
    """What Kavya says this turn, plus the routing/empathy metadata behind it."""

    reply_text: str
    bucket: Bucket | None
    dialogue_state_name: str
    empathy_directive: EmpathyDirective


class DialogueResponseEngine:
    """Deterministic scripted-response FSM: AWAIT_IDENTITY -> CONVERSATION -> CLOSE.

    Stateless itself — all per-call state lives on the ConversationSessionState
    passed into generate_reply(), consistent with every other engine in the
    CIL pipeline (engines are pure functions of their inputs; state is the
    caller's responsibility).
    """

    def __init__(self) -> None:
        self._empathy = EmpathyDirectiveComposer()
        self._register_guard = RegisterGuard()

    def generate_reply(
        self,
        session: DialogueSessionState,
        response_plan: ResponsePlan,
        context: CustomerContext | None,
        user_text: str,
        lender_name: str,
    ) -> DialogueTurnOutput:
        outstanding_minor = _resolve_outstanding_minor(context, response_plan)
        customer_name = context.primary_party.name if context is not None else ""

        state = session.dialogue_state_name
        if state == "AWAIT_IDENTITY":
            reply, bucket = self._handle_await_identity(session, user_text, customer_name, outstanding_minor)
        elif state == "CLOSE":
            reply, bucket = SCRIPT_TEMPLATES["close_farewell"], None
        else:
            reply, bucket = self._handle_conversation(session, user_text, response_plan, outstanding_minor, lender_name)

        directive = self._empathy.compose(user_text, bucket.value if bucket is not None else "")
        reply = self._empathy.apply_to_reply(reply, directive)
        # Rule 6 (never address by name) only applies once identity has been
        # established — the AWAIT_IDENTITY gate must be able to say the
        # customer's name (identity_reask) to confirm it's speaking to the
        # right person in the first place.
        scrub_name = "" if state == "AWAIT_IDENTITY" else customer_name
        reply = self._apply_guards(reply, scrub_name, outstanding_minor)

        session.record_assistant_reply(reply)
        session.set_last_empathy_state(directive.state.value)

        return DialogueTurnOutput(
            reply_text=reply,
            bucket=bucket,
            dialogue_state_name=session.dialogue_state_name,
            empathy_directive=directive,
        )

    def build_hangup_reply(self) -> str:
        return HANGUP_TEXT

    # ------------------------------------------------------------------
    # AWAIT_IDENTITY
    # ------------------------------------------------------------------

    def _handle_await_identity(
        self,
        session: DialogueSessionState,
        user_text: str,
        customer_name: str,
        outstanding_minor: int,
    ) -> tuple[str, Bucket | None]:
        verdict = _classify_identity_response(user_text)
        if verdict == "confirm":
            session.set_identity_verified(True)
            session.set_dialogue_state_name("CONVERSATION")
            session.set_last_ask("when_pay")
            return SCRIPT_TEMPLATES["identity_confirm"].format(outstanding=format_rupees(outstanding_minor)), None
        if verdict == "deny":
            session.set_dialogue_state_name("CLOSE")
            session.set_farewell_requested(True)
            return SCRIPT_TEMPLATES["identity_deny"], None
        if not session.identity_reprompted:
            session.set_identity_reprompted(True)
            return SCRIPT_TEMPLATES["identity_reask"].format(customer_name=customer_name), None
        # Ambiguous a second time — proceed rather than loop the customer
        # through an identity gate indefinitely.
        session.set_dialogue_state_name("CONVERSATION")
        session.set_last_ask("when_pay")
        return SCRIPT_TEMPLATES["identity_confirm"].format(outstanding=format_rupees(outstanding_minor)), None

    # ------------------------------------------------------------------
    # CONVERSATION
    # ------------------------------------------------------------------

    def _handle_conversation(
        self,
        session: DialogueSessionState,
        user_text: str,
        response_plan: ResponsePlan,
        outstanding_minor: int,
        lender_name: str,
    ) -> tuple[str, Bucket]:
        entities = response_plan.entities
        new_date_this_turn = "PROMISE_DATE" in entities or "DATE" in entities
        new_amount_this_turn = "AMOUNT" in entities or "PARTIAL_AMOUNT" in entities
        self._update_ledger_from_entities(session, response_plan, user_text)

        bucket = classify_bucket(
            user_text,
            response_plan,
            last_ask=session.last_ask,
            new_date_this_turn=new_date_this_turn,
            new_amount_this_turn=new_amount_this_turn,
        )

        if bucket == Bucket.FAREWELL:
            session.set_dialogue_state_name("CLOSE")
            return SCRIPT_TEMPLATES["close_farewell"], bucket

        if bucket == Bucket.LOAN_DENIAL:
            session.set_dialogue_state_name("CLOSE")
            return SCRIPT_TEMPLATES["close_callback"], bucket

        if bucket == Bucket.ASK_WHO:
            session.set_last_ask("when_pay")
            return SCRIPT_TEMPLATES["ask_who"].format(agent_name=AGENT_NAME, lender_name=lender_name), bucket

        if bucket == Bucket.ASK_AMOUNT:
            session.set_last_ask("when_pay")
            return SCRIPT_TEMPLATES["ask_amount"].format(outstanding=format_rupees(outstanding_minor)), bucket

        if bucket == Bucket.HARDSHIP:
            session.set_last_ask("part_payment")
            return SCRIPT_TEMPLATES["hardship"], bucket

        if bucket == Bucket.GIVES_DATE:
            session.set_last_ask("confirm_date")
            date_text = str(session.commitment.get("date") or "")
            tpl_key = "gives_date_confirm_relative" if date_is_already_terminated(date_text) else "gives_date_confirm"
            return SCRIPT_TEMPLATES[tpl_key].format(date=date_text), bucket

        if bucket == Bucket.GIVES_AMOUNT:
            return self._handle_gives_amount(session, user_text, outstanding_minor), bucket

        if bucket == Bucket.ACK:
            last = session.last_ask
            if last in ("confirm_date", "confirm_plan"):
                session.set_dialogue_state_name("CLOSE")
                return SCRIPT_TEMPLATES["close_soft"], bucket
            session.set_last_ask("when_pay")
            return SCRIPT_TEMPLATES["anchor"].format(outstanding=format_rupees(outstanding_minor)), bucket

        session.set_last_ask("when_pay")
        return SCRIPT_TEMPLATES["anchor"].format(outstanding=format_rupees(outstanding_minor)), bucket

    def _handle_gives_amount(self, session: DialogueSessionState, user_text: str, outstanding_minor: int) -> str:
        amount_minor = session.commitment.get("amount_minor")
        if not amount_minor:
            session.set_last_ask("when_pay")
            return SCRIPT_TEMPLATES["anchor"].format(outstanding=format_rupees(outstanding_minor))
        cadence = _detect_cadence(user_text)
        plan = compute_installment_plan(outstanding_minor, int(amount_minor), cadence)
        session.update_commitment(cadence=cadence)
        session.set_last_ask("confirm_plan")
        return self._render_plan(plan)

    def _render_plan(self, plan: InstallmentPlan) -> str:
        if plan.kind == InstallmentPlanKind.MONTHLY:
            return SCRIPT_TEMPLATES["plan_computed"].format(
                amount=format_rupees(plan.amount_minor), months=plan.months
            )
        if plan.kind == InstallmentPlanKind.LUMPSUM_FULL:
            return SCRIPT_TEMPLATES["plan_lumpsum_full"].format(amount=format_rupees(plan.amount_minor))
        return SCRIPT_TEMPLATES["plan_lumpsum_partial"].format(
            amount=format_rupees(plan.amount_minor),
            remaining=format_rupees(plan.remaining_minor or 0),
        )

    def _update_ledger_from_entities(
        self,
        session: DialogueSessionState,
        response_plan: ResponsePlan,
        user_text: str,
    ) -> None:
        entities = response_plan.entities
        amount_minor: int | None = None
        if "AMOUNT" in entities:
            amount_minor = int(entities["AMOUNT"]) * 100
        elif "PARTIAL_AMOUNT" in entities:
            amount_minor = int(entities["PARTIAL_AMOUNT"]) * 100

        date_text: str | None = None
        iso_date = entities.get("PROMISE_DATE") or entities.get("DATE")
        if iso_date:
            date_text = _format_date_for_speech(str(iso_date))

        cadence = _detect_cadence(user_text) if amount_minor is not None else None

        if amount_minor is not None or date_text is not None or cadence is not None:
            session.update_commitment(amount_minor=amount_minor, date_text=date_text, cadence=cadence)

    # ------------------------------------------------------------------
    # Register/name/tone cleanup — defense-in-depth on our own templates.
    # ------------------------------------------------------------------

    def _apply_guards(self, reply: str, customer_name: str, outstanding_minor: int) -> str:
        cleaned = dedupe_name(reply, customer_name)
        cleaned = sanitize_reply(cleaned)
        cleaned = strip_trailing_sir(cleaned)
        result = self._register_guard.check(cleaned)
        if not result.clean:
            logger.warning(
                "DialogueResponseEngine: scripted reply failed RegisterGuard (%s); falling back to anchor",
                result.violation,
            )
            return SCRIPT_TEMPLATES["anchor"].format(outstanding=format_rupees(outstanding_minor))
        return cleaned


__all__ = ["DialogueResponseEngine", "DialogueTurnOutput"]
