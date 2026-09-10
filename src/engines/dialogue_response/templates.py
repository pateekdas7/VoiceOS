"""Scripted reply templates for the golden path (Path-A Phase 6f).

Ported from evaluation/founder-validation/conv_server.py's SCRIPT_TEMPLATES.
Unlike that version — which hardcoded "fifty thousand" (one demo customer's
outstanding balance) into the template text — every fact-bearing template
here takes an {outstanding} placeholder filled from CustomerContext /
ResponsePlan.facts at render time (RI-5: facts come from the authoritative
source, never from a literal in code).

Templates are written at world-class salesman level:
- Empathy before ask
- Legitimate consequence framing (CIBIL, daily interest) — no threats
- Option closing (two dates, not open questions)
- Small-win laddering (part payment > nothing)
- Urgency without coercion

Architecture: V2 Ch13 (dialogue state); RI-5 (Law of Authority).
"""

from __future__ import annotations


def format_rupees(amount_minor: int) -> str:
    """Format minor currency units as a spoken-friendly INR string."""
    rupees = amount_minor // 100
    return f"{rupees:,}"


SCRIPT_TEMPLATES: dict[str, str] = {
    # ── Identity gate (turn 1) ────────────────────────────────────────────
    "identity_confirm": (
        "Haan sir — account pe {outstanding} outstanding hai, "
        "CIBIL pe asar pad raha hai — aaj sort kar lete hain, kab tak ho sakta hai?"
    ),
    "identity_reask": (
        "माफ़ कीजिए sir, क्या आप {customer_name} जी बात कर रहे हैं?"
    ),
    "identity_deny": (
        "माफ़ कीजिए, wrong number लग गया। Thanks।"
    ),

    # ── Golden-path buckets (post-identity) ───────────────────────────────
    "ask_who": (
        "मैं {agent_name} हूँ, {lender_name} से — "
        "account matter ke baare mein baat karni thi, 5 min milenge?"
    ),
    "ask_amount": (
        "Sir, {outstanding} outstanding hai account pe — "
        "10 ko ya 15 ko, kaunsa better rahega payment ke liye?"
    ),

    # ── Hardship — empathy first, then small-win ask ──────────────────────
    "hardship": (
        "Samajh sakti hoon, bilkul — kuch bhi possible hai is week? "
        "Even thoda sa bhi chalega."
    ),

    # ── Date confirmation ─────────────────────────────────────────────────
    "gives_date_confirm": (
        "Note kar liya — {date} full payment — confirm hai sir?"
    ),
    "gives_date_confirm_relative": (
        "Note kar liya — {date} full payment confirm kar rahe hain?"
    ),

    # ── Amount / plan ─────────────────────────────────────────────────────
    "gives_amount_plan": (
        "{amount} monthly — pehli installment 5 ko ya 10 ko?"
    ),
    "plan_computed": (
        "Theek hai — {amount} monthly pe lagbhag {months} mahine mein full clear. "
        "Pehli installment kab karein?"
    ),
    "plan_lumpsum_full": (
        "Ek baar mein {amount} — account full clear ho jaayega. Confirm karein?"
    ),
    "plan_lumpsum_partial": (
        "{amount} aaj, {remaining} baad mein — baaki kab tak ho jaayega?"
    ),

    # ── Stalling / next-month objection ───────────────────────────────────
    "stall_next_month": (
        "Bilkul — lekin CIBIL pe mark aa raha hai, "
        "ek date note kar lein toh file peace mein rahegi — 5 ko ya 10 ko?"
    ),

    # ── CIBIL urgency (for repeat stalling) ───────────────────────────────
    "cibil_urgency": (
        "Har din interest add ho raha hai sir — "
        "aaj confirm ho jaaye toh aage nahi badhega."
    ),

    # ── Anchor for anything unmatched ─────────────────────────────────────
    "anchor": (
        "Sir, {outstanding} outstanding hai account pe — "
        "10 ko ya 15 ko, kaunsa better rahega?"
    ),
    # ── No authoritative outstanding-balance available ────────────────────
    # RI-5: asserting any amount here would be fabrication. This template
    # asks the customer to identify their loan without ever committing to
    # a number, and never contains an {outstanding} placeholder. Used only
    # when both CustomerContext.primary_loan AND
    # response_plan.facts['outstanding_balance_minor'] are absent — i.e.
    # the authoritative CRM/Collections lookup failed for this call.
    "clarify_no_record": (
        "Sir, आपके account का detail अभी हमारे पास नहीं है — "
        "क्या आप अपना loan account number बता सकते हैं?"
    ),
    # ── Close scripts ─────────────────────────────────────────────────────
    "close_soft": (
        "Perfect sir — commitment note kar liya. "
        "Our side se koi call nahi aayegi. Thanks, baat hui."
    ),
    "close_farewell": (
        "Theek hai sir, hum baad mein baat karte hain. Thanks!"
    ),
    "close_callback": (
        "Theek hai sir, team se check kara ke callback kar deti hoon."
    ),
}
"""Every state has a canonical response that fires without an LLM call."""

_DATE_ALREADY_TERMINATED_MARKERS = (
    "mein", "में", "tak", "तक", "andar", "अंदर", "par", "पर",
    "baad", "बाद", "end", "shaam", "शाम", "subah", "सुबह", "raat", "रात",
)  # fmt: skip


def date_is_already_terminated(date_text: str) -> bool:
    """True when a date phrase already carries a postposition/terminator
    ("15 din mein", "agle hafte tak") — using gives_date_confirm's "तक" after
    it would produce an ungrammatical double postposition ("15 din mein तक")."""
    lower = date_text.lower()
    return any(marker in date_text or marker.lower() in lower for marker in _DATE_ALREADY_TERMINATED_MARKERS)


__all__ = ["SCRIPT_TEMPLATES", "date_is_already_terminated", "format_rupees"]
