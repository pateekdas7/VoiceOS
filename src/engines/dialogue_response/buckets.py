"""Bucket router for the scripted-response golden path (Path-A Phase 6f).

Ported from evaluation/founder-validation/conv_server.py's _classify_bucket()
(v3.12/v3.13/v3.14 bucket router) — every post-identity turn maps to exactly
one bucket, which DialogueResponseEngine turns into a scripted reply with no
LLM call.

Unlike conv_server.py's classify_intent(), this module does NOT reimplement
intent classification: FAREWELL/LOAN_DENIAL/HARDSHIP are read straight off
ResponsePlan.intents (the real IntentEngine's ranked output — IntentLabel.
DISCONNECT/DISPUTE/HARDSHIP). Only two things are classified locally, because
no existing engine produces them:

  - ASK_WHO / WHY_CALLED / ASK_AMOUNT: fine-grained routing questions
    ("who is this", "why are you calling", "how much do I owe") that sit
    below IntentEngine's 13-label taxonomy — there is no IntentLabel for
    "customer is asking a data question about the outstanding amount".
  - ACK: a bare turn-level affirmation ("haan", "theek hai", "ok") — a
    speech-act signal IntentEngine does not classify as a distinct label
    (it would fall under OTHER).

GIVES_DATE / GIVES_AMOUNT priority is driven by ResponsePlan.entities (the
real EntityExtractor's PROMISE_DATE/DATE/AMOUNT output, Phase 6a), never by
a second parser — see DialogueResponseEngine._new_date_this_turn()/
_new_amount_this_turn().

Architecture: V2 Ch13 (dialogue state); V2 Ch3 (IntentEngine).
"""

from __future__ import annotations

import re
from enum import Enum

from src.libs.contracts.response_plan import IntentLabel, ResponsePlan


class Bucket(str, Enum):
    FAREWELL = "farewell"
    LOAN_DENIAL = "loan_denial"
    REPEAT = "repeat"
    ASK_WHO = "ask_who"
    ASK_AMOUNT = "ask_amount"
    HARDSHIP = "hardship"
    GIVES_DATE = "gives_date"
    GIVES_AMOUNT = "gives_amount"
    ACK = "ack"
    ELSE = "else"


# ---------------------------------------------------------------------------
# Narrow local routing classifiers — no IntentLabel equivalent exists.
# ---------------------------------------------------------------------------

_IDENTITY_QUESTIONS = (
    "आप कौन", "who is this", "who are you", "आपका नाम",
    "आप कहां से", "कहां से बोल", "बताइए कौन",
    "कौन बोल", "कौन हैं आप", "कौन बात कर",
    "aap kaun", "kaun ho", "kaun bol", "kaun hain",
    "kaunsi company", "kaun si company", "kaun company",
    "company se", "konsi company", "which company",
)  # fmt: skip
_IDENTITY_NEGATIVE = ("कौन सा", "कौनसा")
_WHY_CALLED = ("क्यों call", "क्यों फोन", "मुझे क्यों", "why did you", "why calling")
_REPEAT_REQUEST_TOKENS = (
    # Deliberately narrow to unambiguous "say it again" / "I didn't HEAR
    # you" phrasing only. Broader comprehension phrases like "समझ नहीं आया"
    # ("I didn't understand [any of that]") are NOT included here -- that
    # exact phrase is this engine's own canonical example of a genuinely
    # unclassifiable utterance that should fall through to Bucket.ELSE and,
    # after a second consecutive occurrence, trigger the real LLM fallback
    # (_ELSE_FALLBACK_THRESHOLD) -- a customer who is lost needs the LLM's
    # flexibility, not a verbatim repeat of a line they already didn't
    # follow. "sunai nahi"/"suna nahi" ("didn't HEAR") is kept because it is
    # unambiguously an audio/repeat request, not a comprehension one.
    "फिर से", "फिर से बोलो", "फिर से बोलिए", "दोबारा", "दुबारा", "वापस बोलो",
    "सुनाई नहीं", "सुना नहीं", "क्या बोला", "क्या कहा",
    "एक बार फिर", "फिर बोलो", "फिर बताओ",
    "phir se", "phir se bolo", "phirse bolo", "dobara", "dubara", "wapas bolo",
    "sunai nahi", "suna nahi",
    "kya bola", "kya kaha", "phir bolo", "phir batao", "ek baar phir",
    "repeat", "say again", "come again", "one more time", "pardon",
    "excuse me", "what did you say", "can you repeat",
)  # fmt: skip
_AMOUNT_QUERY_TOKENS = (
    "कितना payment", "कितना pay", "कितना पेमेंट", "कितना paisa",
    "कितना बाकी", "कितना outstanding", "कितना दे", "कितना देना",
    "kitna payment", "kitna pay", "kitna baaki", "kitna outstanding",
    "kitna dena", "kitna paisa", "how much", "how much do i",
    "amount kitna", "amount क्या", "इतना पेमेंट", "itna payment", "कितना पैसा",
)  # fmt: skip

_ACK_TOKENS = frozenset({
    "अच्छा", "ठीक", "हाँ", "हां", "ok", "okay", "ओके", "ठीक है",
    "yes", "yeah", "जी", "सही",
    "achha", "acha", "accha", "haan", "haanji", "haan ji",
    "theek", "thik", "thik hai", "theek hai", "confirm",
    "confirmed", "done", "sahi", "bilkul",
})  # fmt: skip

_AFFIRM_AFTER_CONFIRM_RE = re.compile(
    r"^(?:yes|yas|yaas|yeah|yep|ya|yup|ok|okay|"
    r"theek|thik|sahi|done|pakka|confirm|"
    r"haan|haanji|han|hanji|ji|hmm+|hm+)"
    r"[\s\.,!\?।]*$",
    re.IGNORECASE,
)
_AFFIRM_DEVA_RE = re.compile(
    r"^\s*(?:हां|हाँ|हांजी|हाँजी|जी|ठीक|ठीक\s+है|"
    r"पक्का|सही|यस|यास|हम+|हाँ+)"
    r"[\s\.,!\?।]*$",
)


def _strip_devanagari_punct(text: str) -> str:
    return re.sub(r"[।,.!?]+", " ", text).strip()


def _is_identity_question(text: str) -> bool:
    lower = _strip_devanagari_punct(text.lower())
    if any(neg in lower for neg in _IDENTITY_NEGATIVE):
        return False
    return any(tok in lower for tok in _IDENTITY_QUESTIONS)


def _is_why_called(text: str) -> bool:
    lower = _strip_devanagari_punct(text.lower())
    return any(tok in lower for tok in _WHY_CALLED)


def is_repeat_request(text: str) -> bool:
    """True when the customer is asking Kavya to say her last utterance
    again ("phir se bolo", "samajh nahi aaya", "repeat please") — used both
    by classify_bucket() (mid-conversation) and DialogueResponseEngine's
    AWAIT_IDENTITY handling (before identity is confirmed, where no
    ResponsePlan-derived bucket routing runs at all)."""
    lower = _strip_devanagari_punct(text.lower())
    return any(tok in text or tok.lower() in lower for tok in _REPEAT_REQUEST_TOKENS)


def _is_amount_query(text: str) -> bool:
    lower = text.lower()
    return any(tok in text or tok.lower() in lower for tok in _AMOUNT_QUERY_TOKENS)


def _is_bare_ack(text: str) -> bool:
    words = _strip_devanagari_punct(text.lower()).split()
    return len(words) <= 3 and any(w in _ACK_TOKENS for w in words)


def _is_affirmation_after_confirm(text: str) -> bool:
    norm = (text or "").strip().lower()
    return bool(_AFFIRM_AFTER_CONFIRM_RE.search(norm) or _AFFIRM_DEVA_RE.search(text or ""))


def _top_intent_label(response_plan: ResponsePlan) -> IntentLabel | None:
    if not response_plan.intents:
        return None
    return response_plan.intents[0].label


def classify_bucket(
    user_text: str,
    response_plan: ResponsePlan,
    *,
    last_ask: str,
    new_date_this_turn: bool,
    new_amount_this_turn: bool,
) -> Bucket:
    """Route one post-identity turn to exactly one scripted-reply bucket.

    Args:
        user_text: The customer's transcribed utterance this turn.
        response_plan: The sealed ResponsePlan for this turn (real IntentEngine
            + EntityExtractor output).
        last_ask: ConversationSessionState.last_ask — what the agent asked for
            last turn (when_pay/confirm_date/confirm_plan/part_payment).
        new_date_this_turn: True when EntityExtractor resolved a PROMISE_DATE/
            DATE entity this turn that isn't already the ledger's date.
        new_amount_this_turn: True when EntityExtractor resolved an AMOUNT
            entity this turn that isn't already the ledger's amount.
    """
    top = _top_intent_label(response_plan)

    if top == IntentLabel.DISCONNECT:
        return Bucket.FAREWELL
    if top == IntentLabel.DISPUTE:
        return Bucket.LOAN_DENIAL
    if is_repeat_request(user_text):
        return Bucket.REPEAT
    if _is_identity_question(user_text) or _is_why_called(user_text):
        return Bucket.ASK_WHO
    if _is_amount_query(user_text):
        return Bucket.ASK_AMOUNT
    # A concrete date/amount commitment in THIS utterance beats a co-occurring
    # hardship marker — mirrors conv_server.py v3.13 (a customer saying
    # "abhi paise nahi, agle hafte tak" must not lose the date to hardship).
    if new_date_this_turn:
        return Bucket.GIVES_DATE
    if new_amount_this_turn:
        return Bucket.GIVES_AMOUNT
    if top == IntentLabel.HARDSHIP:
        return Bucket.HARDSHIP
    if _is_bare_ack(user_text):
        return Bucket.ACK
    # A bare affirmation after we've explicitly asked for a confirmation must
    # close the call, not fall through to ELSE and get re-anchored.
    if last_ask in ("confirm_date", "confirm_plan") and _is_affirmation_after_confirm(user_text):
        return Bucket.ACK
    return Bucket.ELSE


__all__ = ["Bucket", "classify_bucket", "is_repeat_request"]
