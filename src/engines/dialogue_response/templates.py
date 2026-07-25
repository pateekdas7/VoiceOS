"""Scripted reply templates for the golden path (Path-A Phase 6f).

Ported from evaluation/founder-validation/conv_server.py's SCRIPT_TEMPLATES.
Unlike that version — which hardcoded "fifty thousand" (one demo customer's
outstanding balance) into the template text — every fact-bearing template
here takes an {outstanding} placeholder filled from CustomerContext /
ResponsePlan.facts at render time (RI-5: facts come from the authoritative
source, never from a literal in code).

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
        "Perfect sir, आपके account पर {outstanding} outstanding है — "
        "कब तक clear हो जाएगा?"
    ),
    "identity_reask": (
        "माफ़ कीजिए sir, क्या आप {customer_name} जी बात कर रहे हैं?"
    ),
    "identity_deny": (
        "माफ़ कीजिए, wrong number लग गया। Thanks।"
    ),
    # ── Golden-path buckets (post-identity) ───────────────────────────────
    "ask_who": (
        "मैं {agent_name} हूँ, {lender_name} से। कब तक payment हो जाएगा sir?"
    ),
    "ask_amount": (
        "आपके account पर {outstanding} outstanding है sir। कब तक clear हो जाएगा?"
    ),
    "hardship": (
        "समझ सकती हूँ sir। छोटा part payment इस week possible है क्या?"
    ),
    "gives_date_confirm": (
        "Note kar liya — {date} तक full payment confirm कर रहे हैं sir?"
    ),
    "gives_date_confirm_relative": (
        "Note kar liya — {date} full payment confirm कर रहे हैं sir?"
    ),
    "gives_amount_plan": (
        "{amount} monthly पर plan bana dete hain — kitne mahine mein clear कर सकते हैं?"
    ),
    "plan_computed": (
        "{amount} monthly पर लगभग {months} महीने में full clear हो जाएगा। ठीक है sir?"
    ),
    "plan_lumpsum_full": (
        "Ok, एक बार में {amount} pay कर देंगे तो account clear हो जाएगा।"
    ),
    "plan_lumpsum_partial": (
        "Ok, {amount} pay कर देंगे तो {remaining} बाकी रहेगा — बाकी कब तक?"
    ),
    # ── Anchor for anything unmatched ─────────────────────────────────────
    "anchor": (
        "Sir, आपके account पर {outstanding} outstanding है — "
        "कब तक payment कर सकते हैं?"
    ),
    # ── Close scripts ─────────────────────────────────────────────────────
    "close_soft": (
        "Theek hai sir, आपका commitment note कर लिया। Thanks, बात हुई।"
    ),
    "close_farewell": (
        "Theek hai sir, thanks। बात हुई।"
    ),
    "close_callback": (
        "Theek hai sir, team से check करा के callback कर देती हूँ।"
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
