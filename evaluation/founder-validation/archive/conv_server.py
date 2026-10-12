#!/usr/bin/env python3
"""
=============================================================================
ARCHIVED — RETIRED FROM PRODUCTION (Path-A Runtime Consolidation Phase 8,
2026-07-25). Kept for historical/reference purposes only. DO NOT DEPLOY.

This script drove the founder-approved Call-001 (Sprint-029 Phase 2,
2026-07-20) as a standalone, self-contained FastAPI webhook running
directly on the GPU node, bypassing src/services/ entirely: STT/TTS were
Twilio-native (<Gather>/<Say> + Amazon Polly), not our Whisper/Veena
services, and the only VoiceOS-repo service it called was the LLM via a
same-host localhost:8000 request.

A pre-Call-002 architecture audit found this script's proven persona/
FSM/guard/empathy logic never ran through the designed production runtime
(ConversationEngine) at all. That logic has since been ported and
redesigned (not just copy-pasted) into first-class Path A code, consuming
real IntentEngine/EntityExtractor/NegotiationEngine output instead of this
script's own parallel parser:

  - src/libs/ai_safety/register_guard.py          <- register/tone guards
  - src/engines/empathy_directive/                 <- EmpathyDirectiveComposer
  - src/engines/prompt_builder/kavya_persona.py    <- persona/greeting/hangup
  - src/engines/dialogue_response/                 <- the scripted-reply FSM
  - src/services/conversation_engine/session_state.py <- the commitment ledger

ConversationEngine (src/services/conversation_engine/engine.py), wired
through the real composition root (deployment/cpu/app.py) and the real
Twilio Media Streams WS entrypoint (src/services/media_gateway/
twilio_ws_entrypoint.py), is now the sole production runtime — validated
against real GPU TTS + real Postgres in a live multi-turn dry run (Path-A
Phase 7). No live process runs this script anymore; it was never part of
the current GPU node's reproducible deployment (deployment/gpu/), and its
Call-001 Twilio webhook was an ephemeral trycloudflare.com tunnel, not a
persisted config. Full narrative in CHANGELOG.md's "Path-A Runtime
Consolidation, Phases 1-7" entry.
=============================================================================

VoiceOS Conversation Webhook — Sprint-029 Phase 2 (v3.9)

v3.9 wires the full VoiceOS Volume 2 Conversation Intelligence Layer:
  - IntentEngine (keyword fallback), EntityExtractor, EmotionIntelligenceEngine
  - RiskEngine, DialoguePolicyEngine
  - StrategyEngine, GoalPlanner
  - NegotiationEngine (conditional)
  - EmpathyPlanner + AdaptiveProsodyEngine (per-turn VoiceConfig)
  - AdaptiveConversationEngine (loop detection)
  - ResponsePlanningEngine (orchestrator) + PromptBuilder
  - OutputEvaluationEngine (fire-and-forget quality score)
  - ConversationStateIntelligence (per-call state machine)

Delivery upgrades over v3.8:
  * Uninterruptible greeting: /welcome plays <Say> outside <Gather>,
    then <Redirect> to /listen which opens the Gather.
  * Polly.Kajal-Neural (hi-IN Neural) replaces Polly.Aditi (Standard).
  * Per-turn rate/pause driven by AdaptiveProsodyEngine (VoiceConfig).
  * Ledger day-parsing (pandrah din / 15 din / 15 days / पंद्रह दिन).
  * Register replacements: "आई हूँ" fragment purged; "कोई नहीं" → "कोई बात नहीं".
  * All v3.8 safety guards retained as defense-in-depth alongside engines.
"""
import json
import logging
import math
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.request
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response

# ─── VoiceOS engine + contract imports ──────────────────────────────────────
# Ensure /opt/voiceos-gpu (or repo root) is on sys.path so `src.engines.*`
# resolves. Both the local repo layout and the GPU deployment place the
# src/ tree one level above bin/.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_GPU_ROOT = Path("/opt/voiceos-gpu")
if _GPU_ROOT.exists() and str(_GPU_ROOT) not in sys.path:
    sys.path.insert(0, str(_GPU_ROOT))

from src.libs.contracts.primitives import (
    AccountId, CustomerId, Currency, Money, PhoneNumber, TenantId,
)
from src.libs.contracts.turn import TurnInput, TurnRole, UtteranceSegment
from src.libs.contracts.context import (
    ConsentStatus, ContactInfo, CustomerContext, LoanSummary,
    OutstandingBalance, PartyInfo,
)
from src.libs.contracts.response_plan import IntentLabel
from src.libs.contracts.streaming import (
    EmpathyConfig, LanguageRegister, Pacing, Sentiment, StressLevel, Tone,
    VoiceConfig,
)
from src.engines.intent.engine import IntentEngine
from src.engines.intent.model import IntentModel
from src.engines.entity_extraction.engine import EntityExtractor
from src.engines.emotion.engine import EmotionIntelligenceEngine
from src.engines.risk.engine import RiskEngine
from src.engines.dialogue_policy.engine import DialoguePolicyEngine
from src.engines.strategy.engine import StrategyEngine
from src.engines.goal_planner.engine import GoalPlanner
from src.engines.negotiation.engine import NegotiationEngine
from src.engines.empathy.engine import EmpathyPlanner
from src.engines.prosody.engine import AdaptiveProsodyEngine
from src.engines.adaptive_conversation.engine import AdaptiveConversationEngine
from src.engines.response_planning.engine import ResponsePlanningEngine
from src.engines.prompt_builder.builder import PromptBuilder
from src.engines.output_evaluation.engine import OutputEvaluationEngine
from src.engines.conversation_state.engine import ConversationStateIntelligence

# v3.14 — permanent empathy layer for the scripted golden path (Volume 2 Ch14
# EmpathyPlanner covers the LLM-authored branch; this composer extends the
# empathy contract onto the deterministic state-machine replies).
from empathy_directive import EmpathyDirectiveComposer, EmpathyState  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("conv")

# ── Config ───────────────────────────────────────────────────────────────────
GREETING_MP3_PATH = Path(os.environ.get(
    "GREETING_MP3_PATH",
    "/tmp/founder-validation/call-001/trial-002/call-001-trial-002-telephony.mp3",
))
PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL",
    "https://cleaners-worship-occupations-iso.trycloudflare.com",
)
LLM_URL = os.environ.get("LLM_URL", "http://localhost:8000/v1/chat/completions")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5-7b-instruct-fp8")
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "6.0"))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "120"))
# v3.7: prosody softened from v3.6 (+22%/+8%) → +10%/+4%. The chirpy-robot
# effect the founder heard was largely driven by aggressive rate + pitch.
POLLY_RATE = os.environ.get("POLLY_RATE", "+10%")
# Fix 10: greeting prosody unified with turn prosody so voice sounds identical.
POLLY_GREETING_RATE = os.environ.get("POLLY_GREETING_RATE", "+10%")
POLLY_PITCH = os.environ.get("POLLY_PITCH", "+4%")
POLLY_GREETING_PITCH = os.environ.get("POLLY_GREETING_PITCH", "+4%")
HISTORY_MAX_TURNS = int(os.environ.get("HISTORY_MAX_TURNS", "8"))
# Extras: speechTimeout=auto for smoother turn-taking.
GATHER_SPEECH_TIMEOUT = os.environ.get("GATHER_SPEECH_TIMEOUT", "auto")
TURN_LOG_DIR = Path(os.environ.get("TURN_LOG_DIR", "/opt/voiceos-gpu/logs/turns"))
TURN_LOG_DIR.mkdir(parents=True, exist_ok=True)

OUTSTANDING_RUPEES = 50000
DUE_DATE_TEXT = "30 July"
CONFIDENCE_LOW = 0.55         # Twilio SpeechConfidence below this → force clarify
AMOUNT_MAX_PLAUSIBLE = 500000  # 10× outstanding → treat as STT garble

# Polly voice — v3.9 switches to Kajal-Neural (hi-IN Neural).
# Neural voices give significantly less-robotic baseline vs Aditi Standard.
# Neural does NOT support <emphasis> or <prosody pitch> — we vary rate + volume.
POLLY_VOICE = os.environ.get("POLLY_VOICE", "Polly.Kajal-Neural")
POLLY_VOICE_LANGUAGE = "hi-IN"

app = FastAPI()

sessions_lock = threading.Lock()
sessions: dict[str, dict[str, Any]] = {}

# ─── Engine singletons (constructed once at import) ──────────────────────────
# ResponsePlanningEngine consumes all 10 core engines by dependency injection.
_intent_model = IntentModel()  # keyword mode; no ONNX file required
INTENT_ENGINE = IntentEngine(_intent_model)
ENTITY_EXTRACTOR = EntityExtractor()
EMOTION_ENGINE = EmotionIntelligenceEngine()
RISK_ENGINE = RiskEngine()
POLICY_ENGINE = DialoguePolicyEngine()
STRATEGY_ENGINE = StrategyEngine()
GOAL_PLANNER = GoalPlanner()
NEGOTIATION_ENGINE = NegotiationEngine()
EMPATHY_PLANNER = EmpathyPlanner()
PROSODY_ENGINE = AdaptiveProsodyEngine()
ADAPTIVE_ENGINE = AdaptiveConversationEngine()
# v3.14: turn-scoped empathy composer (bilingual acknowledgment + prosody dip
# + listening break). Deterministic — never calls the LLM.
EMPATHY_COMPOSER = EmpathyDirectiveComposer()
PROMPT_BUILDER = PromptBuilder()
OUTPUT_EVALUATOR = OutputEvaluationEngine()
RESPONSE_PLANNING = ResponsePlanningEngine(
    intent_engine=INTENT_ENGINE,
    entity_extractor=ENTITY_EXTRACTOR,
    emotion_engine=EMOTION_ENGINE,
    risk_engine=RISK_ENGINE,
    dialogue_policy_engine=POLICY_ENGINE,
    strategy_engine=STRATEGY_ENGINE,
    goal_planner=GOAL_PLANNER,
    negotiation_engine=NEGOTIATION_ENGINE,
    empathy_planner=EMPATHY_PLANNER,
    adaptive_conv_engine=ADAPTIVE_ENGINE,
)

# ─── Authoritative CustomerContext for Prateek Das (LOAN-TEST-001) ───────────
# RI-5 Law of Authority: this is the ONLY source of truth for customer facts.
# The LLM never invents amount, DPD, due date. All numbers flow from here.
CUSTOMER_CONTEXT: CustomerContext = CustomerContext(
    tenant_id=TenantId("founder-demo"),
    customer_id=CustomerId("CUST-PRATEEK-001"),
    primary_party=PartyInfo(
        party_id=CustomerId("CUST-PRATEEK-001"),
        role="BORROWER",
        name="Prateek Das",
        contact=ContactInfo(
            phone_number=PhoneNumber("+919911954448"),
            preferred_language="hi-IN",
        ),
        identity_verified=False,
    ),
    loans=(
        LoanSummary(
            account_id=AccountId("LOAN-TEST-001"),
            product_type="PERSONAL_LOAN",
            outstanding_balance=Money(
                amount_minor=OUTSTANDING_RUPEES * 100, currency=Currency.INR,
            ),
            dpd=0,
            next_emi_date=date(2026, 7, 30),
            next_emi_amount=Money(
                amount_minor=OUTSTANDING_RUPEES * 100, currency=Currency.INR,
            ),
        ),
    ),
    outstanding=OutstandingBalance(
        total_outstanding=Money(
            amount_minor=OUTSTANDING_RUPEES * 100, currency=Currency.INR,
        ),
        total_overdue=Money(amount_minor=0, currency=Currency.INR),
        account_count=1,
    ),
    consent_status=ConsentStatus.GRANTED,
    assembled_at=datetime.now(timezone.utc),
)


# ── System prompt (leaner; heavy lifting is in code, not rules) ──────────────
SYSTEM_PROMPT = (
    "You are Kavya, a female Delhi loan-collections executive at Rajat Finance, "
    "on a live phone call with customer Prateek Das.\n"
    "Loan facts (fixed): outstanding fifty thousand rupees, due 30 July.\n\n"
    "TONE: Warm confident Delhi office-girl. Not formal call-centre, not literary. "
    "Feminine grammar always (कर रही हूँ, दूँगी, बताऊँगी, समझ सकती हूँ).\n\n"
    "RULES:\n"
    "1. One short sentence, ≤18 words. Never truncate mid-thought.\n"
    "2. Delhi Hinglish only: Hindi in देवनागरी, English in Roman (payment, EMI, "
    "account, please, ok, thanks, callback, team, monthly, week, today, tomorrow). "
    "Never write English in Devanagari.\n"
    "3. Never use these formal/literary Hindi words (sound unnatural on a call): "
    "भुगतान (use 'payment'), कृपया (use 'please'), राशि (use 'amount'), वाक्य, "
    "अंतिम, अवशेष, रात्रि, धन्यवाद (use 'thanks'), समक्ष, विवरण, प्रतीत, अवगत.\n"
    "4. Never use slang or rude words: साला, साली, बे, अबे, यार, भोसड़ी, "
    "चूतिया, कमीना. Never rude.\n"
    "5. Address customer as आप. Never तुम, तू. Say 'sir' only inline at start of "
    "a sentence, at most once every few turns. Never as tail filler.\n"
    "6. NEVER say 'Prateek', 'प्रतीक', 'प्रतीक जी'. Use only 'sir'.\n"
    "7. YOU ARE ON THIS PHONE CALL. Cannot visit bank, check system, meet team. "
    "Only allowed check phrase: 'मैं team से check करा के callback करा देती हूँ'.\n"
    "8. NEVER offer follow-ups the customer did not ask for (no 'हर महीने कॉल').\n"
    "9. The server may inject 'ASSISTANT_HINT: …' — treat it as ground truth. "
    "For EMI months, use exactly the number the hint gives. Never compute it "
    "yourself. Never contradict the commitment already on file.\n"
    "10. If the customer text is garbled/unclear, ASK ONE clarification: "
    "'माफ़ कीजिए, ज़रा और बताइए?'. Never guess.\n"
    "11. No markdown, no bullets, no emoji.\n"
    "12. Openers vary: Acha, Toh, Ok, Haan, Theek hai, or plain. Never same "
    "opener two turns in a row.\n"
    "13. FEMININE ONLY: 'बोल रही हूँ' (not बोल रहा हूँ), 'समझ सकती हूँ' (not सकता), "
    "'कर दूँगी' (not दूँगा), 'आई हूँ' (not आया हूँ), 'गई थी' (not गया था). "
    "You are a woman — never use masculine 1st-person verb forms.\n"
    "14. NEVER confirm a specific date or amount unless the customer explicitly "
    "stated it in their latest message. On vague acks (हाँ, ठीक है, ok, अच्छा), "
    "ASK — do not fabricate. Use: 'कौन सी date पे payment हो जाएगा?' — never "
    "invent '30 July तक payment हो जाएगा?'.\n"
    "15. If the customer asks 'कौन सा amount', 'कौन सी date', 'कौन से महीने' — "
    "these are DATA questions about loan details, NOT identity challenges. "
    "Answer with the loan detail; never say 'मैं Kavya हूँ'.\n"
    "16. NEVER announce call recording ('call recording ki ja rahi', "
    "'रिकॉर्डिंग', 'recording ho rahi'). No consent/disclaimer lines. That is "
    "the platform's job, not Kavya's.\n"
    "17. Reply ONLY in Hindi (देवनागरी) and English (Roman). NEVER emit any "
    "other script — no Mandarin/Chinese, no Arabic, no Bengali, no Tamil, "
    "no Marathi-specific script variants.\n"
    "18. If the customer says a relative day like 'pandrah din mein' / "
    "'15 days में' / 'पंद्रह दिन में' — echo it back verbatim ('15 din mein "
    "note कर रही हूँ') instead of asking again for a calendar date.\n\n"
    "STYLE EXAMPLES:\n"
    "Customer: हाँ बताइए\n"
    "Kavya: Sir, आपके account पर fifty thousand outstanding है — कब तक clear हो जाएगा?\n"
    "Customer: अभी तंगी है\n"
    "Kavya: समझ सकती हूँ, कोई नहीं — कितना pay हो सकता है इस week?\n"
    "Customer: 4000 monthly\n"
    "Kavya: [uses ASSISTANT_HINT months] Ok, 4000 monthly पर 13 महीने में full clear हो जाएगा।\n"
    "Customer: 30 August तक\n"
    "Kavya: Theek hai, 30 August तक full payment note कर रही हूँ।\n"
    "Customer: कोई penalty?\n"
    "Kavya: Time पर payment हो जाए तो कोई penalty नहीं।\n"
    "Customer: (garbled)\n"
    "Kavya: माफ़ कीजिए, ज़रा और बताइए?\n"
    "Customer: 50 lakh house loan\n"
    "Kavya: यह मेरे department में नहीं है, team से check करा के callback करा देती हूँ।\n"
    "Customer: (asks who is calling)\n"
    "Kavya: मैं Kavya हूँ, Rajat Finance से।\n"
)

GREETING_TEXT = (
    "नमस्ते sir, मैं Rajat Finance से Kavya बात कर रही हूँ outstanding balance के regarding। "
    "क्या मेरी बात Prateek Das से हो रही है?"
)

HANGUP_TEXT = "Theek hai sir, हम later बात करेंगे। Thanks!"


def now_ms() -> int:
    return int(time.time() * 1000)


def turn_log(payload: dict) -> None:
    call_sid = payload.get("call_sid", "unknown")
    payload.setdefault("wall_ms", now_ms())
    line = json.dumps(payload, ensure_ascii=False)
    log.info("TURN %s", line)
    try:
        with open(TURN_LOG_DIR / f"{call_sid}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception as e:
        log.warning("failed to write turn log: %s", e)


def get_session(call_sid: str) -> dict[str, Any]:
    with sessions_lock:
        sess = sessions.get(call_sid)
        if sess is None:
            sess = {
                "history": [{"role": "system", "content": SYSTEM_PROMPT}],
                "turn": 0,
                "started_ms": now_ms(),
                "meta": {},
                "last_opener": None,
                "assistant_replies": [],
                # commitment ledger — Class 6
                "commitment": {
                    "amount": None,     # int rupees offered as EMI or lumpsum
                    "months": None,     # int months to full clear
                    "date": None,       # promised date text (verbatim)
                    "cadence": None,    # "monthly" | "one-shot" | None
                },
                # v3.9: per-call conversation state (Vol 2 Ch13)
                "state": ConversationStateIntelligence(),
                # v3.9: rolling intent history for AdaptiveConversationEngine
                "intent_history": [],
                # v3.9: last VoiceConfig used (drives per-turn SSML prosody)
                "voice_config": None,
                # v3.9: whether identity was verified this session
                "identity_verified": False,
                # v3.11: deterministic conversation state machine.
                # Values: AWAIT_IDENTITY → DISCUSS_OUTSTANDING → NEGOTIATE_PLAN
                #         → CONFIRM_COMMITMENT → CLOSE.
                # Set to AWAIT_IDENTITY by /welcome; every /respond turn advances
                # per the state handler in respond().
                "state_name": "AWAIT_IDENTITY",
                # v3.11: track recording-hallucination attempts; if the LLM
                # tries twice, drop the LLM route for the rest of the call.
                "hallucination_hits": 0,
                # v3.11: whether we've already asked the identity re-question.
                "identity_reprompted": False,
                # v3.11: whether the customer has been offered a commitment
                # capture question (so we don't repeat "kab tak clear" endlessly).
                "commitment_asked": False,
                # v3.11: farewell requested by the customer this call.
                "farewell_requested": False,
                # v3.11: turns since last state change (safety net for state
                # machine loops).
                "state_turn": 0,
            }
            sessions[call_sid] = sess
        return sess


def drop_session(call_sid: str) -> None:
    with sessions_lock:
        sessions.pop(call_sid, None)


# ── Customer-name scrub (retained from v3.6; still needed) ───────────────────
_NAME_PATTERNS = [
    r",?\s*प्रतीक\s*दास\s*जी\s*",
    r",?\s*प्रतीक\s*जी\s*",
    r",?\s*प्रतीक\s+दास\s*",
    r",?\s*प्रतीक\s+sir\s*",
    r",?\s*प्रतीक\s*",
    r",?\s*Prateek\s+Das\s+sir\s*",
    r",?\s*Prateek\s+Das\s*",
    r",?\s*Pratik\s+Das\s*",
    r",?\s*Prateek\s+sir\s*",
    r",?\s*Prateek\s*",
    r",?\s*Pratik\s*",
]


def dedupe_name(reply: str, sess: dict) -> str:
    changed = reply
    for pat in _NAME_PATTERNS:
        changed = re.sub(pat, " ", changed, flags=re.IGNORECASE)
    changed = re.sub(r"\s+", " ", changed).strip()
    changed = re.sub(r"\s+([।,.?!])", r"\1", changed)
    changed = re.sub(r"^([,.।])\s*", "", changed)
    return changed or reply


# ── Class 1 register-guard: category-based reject-and-replace ────────────────
# Bad categories. If ANY appears in the reply → replace the entire reply with a
# safe context-appropriate template. Substring match is used deliberately so
# inflections like टिप्पणियों / टिप्पणी / टिप्पणि all match with one entry.
# Fix 6: literary blocklist trimmed. Removed: यदि, आवश्यकता, संबंधित, अनुरोध,
# अपेक्षित, प्रतिष्ठा, टिप्पण — these are ordinary Delhi office register.
# Kept: only genuinely Sanskritized words that would sound literary on a call.
_REGISTER_BAD_LITERARY = [
    "भुगतान", "कृपया", "प्रतीत", "अवगत", "राशि", "वाक्य", "अंतिम",
    "अवशेष", "रात्रि", "धन्यवाद", "समक्ष", "विवरण",
]
_REGISTER_BAD_SLANG = [
    "साला", "साली", "अबे", "भोसड़", "चूतिय", "कमीन", "यार ",  # note trailing space
]
# Fix 3: masculine list extended with past-form + auxiliary combos.
_REGISTER_BAD_MASCULINE_ME = [
    # future/present 1st-person
    "कर दूंगा", "दे दूंगा", "करूँगा", "करूंगा", "दूँगा", "दूंगा",
    "बताऊँगा", "बताऊंगा", "लूँगा", "लूंगा", "सकूँगा", "सकूंगा",
    # past-form 1st-person masculine (trial-013 T8: "आया हूँ")
    "आया हूँ", "आया हूं", "गया हूँ", "गया हूं", "हुआ हूँ", "हुआ हूं",
    "बैठा हूँ", "बैठा हूं", "आया था", "गया था", "बोला था",
    # simple past/present markers
    "कर सकता", "बोल रहा हूँ", "बोल रहा हूं", "जा रहा हूँ", "जा रहा हूं",
    "समझ सकता",
    # v3.11 fix C: broken/masc 2nd-person plural verb tails Trial-015 emitted.
    "कर देगे", "कर देगा", "कर देंगा", "कर देगें",
    "कर देगे?", "कर देगा?", "कर देगे।",
    "पे payment कर देगे", "payment कर देगे",
]
_REGISTER_BAD_ACTIONS = [
    "मैं बैंक", "बैंक जा", "system में देख", "अभी जाकर", "मैं system",
    "अपने laptop", "अपनी system",
]
_REGISTER_BAD_UNSOLICITED = [
    "हर महीने कॉल", "हर महीने call", "monthly call", "monthly कॉल",
    "हर महीने बात", "रोज़ कॉल",
]
# v3.9 defect-1: LLM hallucinated "yeh call recording ki ja rahi hai" in trial
# sim02 T1. Call-recording announcements are outside authoritative facts —
# never allowed. Match all common phrasings, in both scripts.
_REGISTER_BAD_HALLUCINATIONS = [
    "call recording", "recording ki ja", "रिकॉर्ड की जा", "रिकॉर्डिंग की जा",
    "record ki ja", "recording ho rahi", "रिकॉर्डिंग हो रही",
    "call record", "call ki recording",
    # policy / discount inventions
    "discount दे सकते", "waiver दे सकते", "interest माफ",
]

_REGISTER_BAD = (
    _REGISTER_BAD_LITERARY
    + _REGISTER_BAD_SLANG
    + _REGISTER_BAD_MASCULINE_ME
    + _REGISTER_BAD_ACTIONS
    + _REGISTER_BAD_UNSOLICITED
    + _REGISTER_BAD_HALLUCINATIONS
)


def register_bad_hit(reply: str) -> str | None:
    """Return the first matching category tag, or None if reply is clean."""
    # v3.9 defect-3: catch CJK / Arabic code-switch (LLM occasionally emits
    # Mandarin like "後支付"). Only Devanagari + Latin + digits + common
    # punctuation are ever legitimate in Kavya's replies.
    if re.search(r"[\u4E00-\u9FFF\u3040-\u30FF\u0600-\u06FF]", reply):
        return "foreign_script"
    lower = reply.lower()
    for cat_name, cat in [
        ("literary", _REGISTER_BAD_LITERARY),
        ("slang", _REGISTER_BAD_SLANG),
        ("masc_me", _REGISTER_BAD_MASCULINE_ME),
        ("action_hallucination", _REGISTER_BAD_ACTIONS),
        ("unsolicited_followup", _REGISTER_BAD_UNSOLICITED),
        ("hallucination", _REGISTER_BAD_HALLUCINATIONS),
    ]:
        for token in cat:
            if token in reply or token.lower() in lower:
                return cat_name
    return None


# ── Class 4 intent classifier + template routing ─────────────────────────────
# v3.9 defect-4: Roman ack tokens added. "Achha, confirm" was previously routed
# to LLM because "achha"/"confirm" were not in the ack set.
_ACK_TOKENS = {"अच्छा", "ठीक", "हाँ", "हां", "ok", "okay", "ओके", "ठीक है",
               "yes", "yeah", "जी", "सही",
               "achha", "acha", "accha", "haan", "haanji", "haan ji",
               "theek", "thik", "thik hai", "theek hai", "confirm",
               "confirmed", "done", "sahi", "bilkul"}
# v3.11 fix A: identity-affirmation tokens (customer confirms they are Prateek).
# Phrases used when responding to "क्या मेरी बात Prateek Das से हो रही है?".
_IDENTITY_YES_TOKENS = [
    "हाँ", "हां", "haan", "yes", "बोल रहा", "bol raha", "speaking",
    "हां जी", "haan ji", "jee", "जी", "जी हाँ", "ji haan",
    "बोलिए", "boliye", "haan boliye", "हाँ बोलिए",
    "yes speaking", "yeah speaking", "Prateek यहाँ", "Prateek here",
    "main Prateek", "मैं Prateek", "मैं प्रतीक",
]
# v3.11 fix A: identity-denial tokens (wrong number / not Prateek).
_IDENTITY_NO_TOKENS = [
    "wrong number", "गलत नंबर", "galat number", "नहीं", "nahi", "nahin",
    "no", "not me", "not Prateek", "मैं नहीं", "main nahi",
    "यह Prateek नहीं", "wrong person", "kaun Prateek",
]
# v3.11 fix D: amount-query intent — "kitna payment", "kitna baaki",
# "kitna outstanding", "how much" — deterministic template response.
_AMOUNT_QUERY_TOKENS = [
    "कितना payment", "कितना pay", "कितना पेमेंट", "कितना paisa",
    "कितना बाकी", "कितना outstanding", "कितना दे", "कितना देना",
    "kitna payment", "kitna pay", "kitna baaki", "kitna outstanding",
    "kitna dena", "kitna paisa", "how much", "how much do i",
    "amount kitna", "amount क्या",
    # trial-015 T2/T3/T4: user re-asked "kitna" many ways
    "इतना पेमेंट", "itna payment", "कितना पैसा",
]
# v3.9 defect-2: HARDSHIP short-circuit. Engine detects HARDSHIP intent; we
# refuse to route to LLM and instead emit a template that acknowledges +
# proposes a concrete relief step (part payment this week).
_HARDSHIP_MARKERS = [
    "paise nahi", "पैसे नहीं", "पैसा नहीं", "money nahi",
    "tabiyat", "तबीयत", "बीमार", "bimar", "beemar",
    "hospital", "अस्पताल", "job chali", "नौकरी", "naukri",
    "ghar mein problem", "family problem", "salary nahi",
    "kharaab", "ख़राब", "खराब", "tangi", "तंगी",
]
_FAREWELL_TOKENS = {"बाय", "bye", "बाई", "अलविदा", "फिर मिलेंगे", "goodbye",
                    "bye bye", "ok bye",
                    # v3.11 fix E: soft-farewell variants — customer wants to
                    # end the call without saying "bye" explicitly.
                    "मैं कॉल करता हूं", "main call karta hoon",
                    "बाद में बात", "baad mein baat", "later baat",
                    "मैं wapas call", "wapas call karta",
                    "अभी रखो", "रख दो call", "cut karo", "call cut",
                    "मैं busy हूँ", "main busy", "call later"}
# Fix 4: identity requires actual identity context. Bare "कौन" alone is
# ambiguous — "कौन सा पेमेंट" (which payment) is a data question, not an
# identity challenge. Match only strong identity-context phrases.
_IDENTITY_QUESTIONS = ["आप कौन", "who is this", "who are you", "आपका नाम",
                       "आप कहां से", "कहां से बोल", "यह Rajat", "बताइए कौन",
                       "कौन बोल", "कौन हैं आप", "कौन बात कर",
                       # v3.12: Roman variants — trial-016 T2 said "aap kaun
                       # ho" and it fell to `open`.
                       "aap kaun", "kaun ho", "kaun bol", "kaun hain",
                       "kaunsi company", "kaun si company", "kaun company",
                       "company se", "konsi company", "which company"]
# Negative-context tokens: if any appear, the utterance is a DATA question,
# not an identity challenge. Demote to `open` even if "कौन" is present.
# v3.12: removed "which company"/"कौन सी" — those ARE identity challenges.
_IDENTITY_NEGATIVE = ["कौन सा", "कौनसा"]
_WHY_CALLED = ["क्यों call", "क्यों फोन", "मुझे क्यों", "why did you", "why calling"]
_LOAN_DENIAL = ["loan नहीं लिया", "लोन नहीं लिया", "मैंने कुछ नहीं लिया",
                "मैंने loan", "कोई loan नहीं"]


def _strip_devanagari_punct(text: str) -> str:
    return re.sub(r"[।,.!?]+", " ", text).strip()


def classify_intent(text: str) -> str:
    """Coarse intent → tag used for template routing."""
    if not text:
        return "empty"
    t = _strip_devanagari_punct(text.lower())
    for tok in _FAREWELL_TOKENS:
        if tok in text or tok.lower() in t:
            return "farewell"
    for tok in _LOAN_DENIAL:
        if tok in t:
            return "loan_denial"
    # v3.11 fix D: amount-query BEFORE identity — data question, not identity.
    lower_text = text.lower()
    for tok in _AMOUNT_QUERY_TOKENS:
        if tok in text or tok.lower() in lower_text:
            return "amount_query"
    # Fix 4: demote identity if a which-adjective ("कौन सा/सी/से") is present.
    has_negative = any(neg in t for neg in _IDENTITY_NEGATIVE)
    if not has_negative:
        for tok in _IDENTITY_QUESTIONS:
            if tok in t:
                return "identity"
    for tok in _WHY_CALLED:
        if tok in t:
            return "why_called"
    # v3.9 defect-2: HARDSHIP before ack.
    for tok in _HARDSHIP_MARKERS:
        if tok in text or tok in lower_text:
            return "hardship"
    words = t.split()
    if len(words) <= 3 and any(w in _ACK_TOKENS for w in words):
        return "ack"
    return "open"


# v3.11 fix A: identity classification for AWAIT_IDENTITY state.
def classify_identity_response(text: str) -> str:
    """Return 'confirm' / 'deny' / 'ambiguous' for the initial identity turn."""
    if not text:
        return "ambiguous"
    lower_text = text.lower()
    for tok in _IDENTITY_NO_TOKENS:
        if tok in text or tok.lower() in lower_text:
            return "deny"
    for tok in _IDENTITY_YES_TOKENS:
        if tok in text or tok.lower() in lower_text:
            return "confirm"
    return "ambiguous"


TEMPLATE_ROUTES = {
    "identity":     "मैं Kavya हूँ, Rajat Finance से।",
    "why_called":   "आपके account पर fifty thousand outstanding है — उसी के बारे में बात करनी थी।",
    "loan_denial":  "Ok, मैं team से check करा के आपको callback करा देती हूँ।",
    "farewell":     "Theek hai, thanks. Bye!",
    "empty":        "माफ़ कीजिए, ज़रा और बताइए?",
    "low_confidence": "माफ़ कीजिए, ज़रा और बताइए?",
    "garbled_amount": "माफ़ कीजिए, कितना बोला आपने?",
    # v3.9 defect-2: acknowledge + propose concrete relief in one line.
    "hardship":     "समझ सकती हूँ sir, कोई बात नहीं। छोटा part payment इस week possible है क्या?",
    # v3.11 fix D: direct answer to "kitna payment / kitna baaki".
    "amount_query": "आपके account पर fifty thousand outstanding है sir।",
}

# v3.11 fix A: state-machine script templates. Every state has a canonical
# response that fires WITHOUT calling the LLM. LLM only runs for open flow.
SCRIPT_TEMPLATES = {
    # ── Identity gate (turn 1) ────────────────────────────────────────────
    "identity_confirm": (
        "Perfect sir, आपके account पर fifty thousand outstanding है — "
        "कब तक clear हो जाएगा?"
    ),
    "identity_reask": (
        "माफ़ कीजिए sir, क्या आप Prateek Das जी बात कर रहे हैं?"
    ),
    "identity_deny": (
        "माफ़ कीजिए, wrong number लग गया। धन्यवाद।"
    ),
    # ── Golden-path buckets (post-identity) ───────────────────────────────
    "ask_who": (
        "मैं Kavya हूँ, Rajat Finance से।"
    ),
    "ask_amount": (
        "आपके account पर fifty thousand outstanding है sir। कब तक clear हो जाएगा?"
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
    # ── Anchor for anything unmatched ─────────────────────────────────────
    "anchor": (
        "Sir, आपके account पर fifty thousand outstanding है — "
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


def ack_reply(sess: dict) -> str:
    """After a bare ack, deterministically move to next collection sub-goal.
    Fix 1: never confirm a specific date the customer has not uttered.
    On first ack (no commitment on file), ASK for date instead of asserting one.
    v3.9 defect-4: when ledger has a relative day-offset ("15 din mein"), echo
    it back verbatim instead of asking for the date again.
    """
    c = sess["commitment"]
    if not c["amount"] and not c["date"]:
        return "Acha, तो कौन सी date तक payment हो जाएगा?"
    if c["date"] and not c["cadence"]:
        # v3.9 defect-4: relative-day phrasing uses "में" naturally, not "तक".
        date_txt = c["date"]
        if "din mein" in date_txt or "दिन में" in date_txt:
            return f"Theek hai, {date_txt} full payment note कर रही हूँ।"
        return f"Theek hai, {date_txt} तक full payment note कर रही हूँ।"
    if c["amount"] and c["months"]:
        return f"Ok, {c['amount']} monthly, {c['months']} महीने note है।"
    return "Ok, note कर लिया। और कुछ बताइए?"


# ── Class 2 EMI calculator + amount parsing ──────────────────────────────────
# Latin/ASCII digit sequence, or all-Devanagari digit sequence.
_LATIN_NUM_RE = re.compile(r"\b(\d{2,7})\b")
_DEVA_NUM_RE = re.compile(r"([\u0966-\u096F]{2,7})")

# Words that hint the number is an EMI (per-month) vs a lumpsum.
_MONTHLY_CUE = ["महीने का", "प्रति महीना", "monthly", "per month", "हर महीने",
                "हर month", "एक महीने में"]
_LUMPSUM_CUE = ["एक बार में", "एक साथ", "एक शॉट में", "one shot", "one-shot",
                "पूरा", "फुल", "full"]

_DEVA_DIGIT = str.maketrans("०१२३४५६७८९", "0123456789")


def parse_amount(text: str) -> int | None:
    """Extract a rupee amount (>=500). Callers filter against AMOUNT_MAX_PLAUSIBLE
    themselves — parse_amount must return implausible values so the STT-garble
    gate can fire (see /respond amount plausibility check)."""
    if not text:
        return None
    normalized = text.translate(_DEVA_DIGIT)
    for m in _LATIN_NUM_RE.finditer(normalized):
        try:
            v = int(m.group(1))
        except ValueError:
            continue
        if v >= 500:
            return v
    return None


def amount_cadence(text: str) -> str | None:
    lower = text.lower()
    for cue in _MONTHLY_CUE:
        if cue in text or cue in lower:
            return "monthly"
    for cue in _LUMPSUM_CUE:
        if cue in text or cue in lower:
            return "one-shot"
    return None


def compute_emi_hint(amount: int, cadence: str | None) -> dict[str, Any]:
    """Return a deterministic EMI plan for the LLM to phrase."""
    out = OUTSTANDING_RUPEES
    if cadence == "one-shot" or (cadence is None and amount >= out):
        if amount >= out:
            return {
                "kind": "lumpsum_full",
                "amount": amount,
                "line": f"Ok, एक बार में fifty thousand pay कर देंगे तो account clear हो जाएगा।",
            }
        # partial lumpsum
        remaining = out - amount
        return {
            "kind": "lumpsum_partial",
            "amount": amount,
            "remaining": remaining,
            "line": f"Ok, {amount} pay कर देंगे तो {remaining} बाकी रहेगा — बाकी कब तक?",
        }
    # monthly
    months = math.ceil(out / amount)
    return {
        "kind": "monthly",
        "amount": amount,
        "months": months,
        "line": f"Ok, {amount} monthly पर {months} महीने में full clear हो जाएगा।",
    }


def build_assistant_hint(user_text: str, sess: dict, confidence: float) -> str | None:
    parts: list[str] = []
    c = sess["commitment"]
    if c["amount"] or c["date"] or c["months"]:
        ledger_bits: list[str] = []
        if c["amount"]:
            ledger_bits.append(f"amount ₹{c['amount']}")
        if c["cadence"]:
            ledger_bits.append(c["cadence"])
        if c["months"]:
            ledger_bits.append(f"{c['months']} महीने")
        if c["date"]:
            ledger_bits.append(f"by {c['date']}")
        parts.append("Commitment on file: " + ", ".join(ledger_bits)
                     + ". Do not propose different terms unless customer explicitly changes.")

    amt = parse_amount(user_text)
    if amt is not None and amt <= AMOUNT_MAX_PLAUSIBLE:
        cadence = amount_cadence(user_text)
        plan = compute_emi_hint(amt, cadence)
        parts.append(f"EMI computed for offered ₹{amt}: {plan['line']}")
        sess["_last_plan"] = plan
    else:
        # implausible or absent — no plan; /respond may already have routed to
        # garbled_amount template before we got here.
        sess["_last_plan"] = None

    if confidence < CONFIDENCE_LOW and confidence > 0:
        parts.append(
            "STT confidence low — if intent is unclear, ask 'माफ़ कीजिए, ज़रा और बताइए?'."
        )

    if not parts:
        return None
    return "ASSISTANT_HINT: " + " ".join(parts)


# ── Class 2 post-check: number reconciliation ────────────────────────────────
# Fix 2: negative lookahead `(?!y)` prevents matching "month" inside "monthly".
# Trial-013 T5: "5000 monthly पर 10 महीने" was destroyed → "10 monthly पर 10
# महीने" because the old regex matched "month" in "monthly" as a months-count
# and rewrote the 5000 amount. Now "monthly" is skipped entirely.
_MONTHS_RE = re.compile(r"(\d+)\s*(?:महीने|महीना|months?(?![a-zA-Z]))")


def reconcile_months(reply: str, plan: dict | None) -> str:
    if not plan or plan.get("kind") != "monthly":
        return reply
    correct = plan["months"]
    def sub(m: re.Match) -> str:
        try:
            spoken = int(m.group(1))
        except ValueError:
            return m.group(0)
        if spoken != correct:
            return f"{correct} " + m.group(0).split(None, 1)[1] if " " in m.group(0) else f"{correct} महीने"
        return m.group(0)
    return _MONTHS_RE.sub(sub, reply)


# Fix 2 (defence-in-depth): the reply must never mention an amount that isn't
# the authoritative outstanding OR the customer's offered plan amount. If a
# stray number like 10 or 100 appears where a monetary amount is expected,
# replace the whole reply with a safe fallback.
_AMOUNT_IN_REPLY_RE = re.compile(
    r"(\d{2,7})\s*(?:रुपए|रूपए|रुपये|rupay|rupees|Rs\.?|₹|हज़ार|हजार|thousand|लाख|lakh)",
    re.IGNORECASE,
)
_AMOUNT_SAFE_FALLBACK = (
    "आपका outstanding amount fifty thousand rupees है। "
    "क्या आप इस payment के बारे में बात कर सकते हैं?"
)


# Fix 1 (defence-in-depth): reply must not confirm a specific date unless the
# customer either uttered it in their latest message or it's already on the
# ledger. If Kavya invents a date, replace with a question.
_DATE_STRIP_QUESTION = "कौन सी date तक payment हो जाएगा?"


def guard_reply_date(reply: str, user_text: str, sess: dict) -> tuple[str, str]:
    match = _DATE_LIKE_RE.search(reply)
    if not match:
        return reply, "date_ok"
    reply_date = match.group(1).strip().lower()
    # Allowed if customer said it in this turn or previously on the ledger.
    if user_text and reply_date in user_text.lower():
        return reply, "date_ok"
    ledger_date = (sess.get("commitment") or {}).get("date") or ""
    if ledger_date and reply_date in ledger_date.lower():
        return reply, "date_ok"
    log.warning("CRITICAL: reply invents date %r not stated by customer: %s",
                reply_date, reply)
    return _DATE_STRIP_QUESTION, f"date_bad:{reply_date}"


def guard_reply_amounts(reply: str, plan: dict | None) -> tuple[str, str]:
    """Verify any amount mentioned in reply is authoritative.
    Returns (final_reply, amount_guard_action).
    """
    allowed = {OUTSTANDING_RUPEES}
    if plan and plan.get("amount"):
        allowed.add(int(plan["amount"]))
        rem = plan.get("remaining")
        if rem:
            allowed.add(int(rem))
    for m in _AMOUNT_IN_REPLY_RE.finditer(reply):
        try:
            val = int(m.group(1))
        except ValueError:
            continue
        if val < 500:
            # something like "10 rupees" — implausibly small monetary amount
            log.warning("CRITICAL: reply contains implausible amount %s: %s",
                        val, reply)
            return _AMOUNT_SAFE_FALLBACK, f"amount_bad:{val}"
        if val not in allowed:
            log.warning("CRITICAL: reply contains unauthorized amount %s "
                        "(allowed=%s): %s", val, allowed, reply)
            return _AMOUNT_SAFE_FALLBACK, f"amount_bad:{val}"
    return reply, "amount_ok"


# ── Class 6 commitment ledger update ─────────────────────────────────────────
_DATE_LIKE_RE = re.compile(
    r"(\d{1,2}\s*(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December|जनवरी|फ़रवरी|मार्च|अप्रैल|मई|जून|जुलाई|अगस्त|"
    r"सितंबर|अक्टूबर|नवंबर|दिसंबर))",
    re.IGNORECASE,
)
# v3.11 fix B: "N तारीख" / "N tareekh" / "N tarikh" — day-of-month promises.
# Trial-015 T6 captured a "29 तारीख" commit that ledger missed because the
# original regex required a month word.
_TAREEKH_RE = re.compile(
    r"(\d{1,2})\s*(?:तारीख|tareekh|tarikh|taarikh)",
    re.IGNORECASE,
)

# v3.9: relative-day ledger — customers frequently say "pandrah din mein",
# "15 din", "15 days", "पंद्रह दिन". Capture the day count so the commitment
# ledger records a promise even when no calendar date is uttered.
_HINDI_DAY_WORDS: dict[str, int] = {
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5,
    "छह": 6, "छः": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
    "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15,
    "सोलह": 16, "सत्रह": 17, "अठारह": 18, "उन्नीस": 19, "बीस": 20,
    "इक्कीस": 21, "बाईस": 22, "तेईस": 23, "चौबीस": 24, "पच्चीस": 25,
    "तीस": 30, "पैंतीस": 35, "चालीस": 40, "पैंतालीस": 45, "पचास": 50,
    "साठ": 60,
}
_HINGLISH_DAY_WORDS: dict[str, int] = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5,
    "chhah": 6, "chhe": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10,
    "gyarah": 11, "barah": 12, "terah": 13, "chaudah": 14,
    "pandrah": 15, "pandra": 15,
    "solah": 16, "satrah": 17, "atharah": 18, "unnis": 19, "bees": 20,
    "ikkis": 21, "bais": 22, "tees": 30, "pachaas": 50, "pachas": 50,
}
_DAY_UNIT_RE = re.compile(
    r"(\d{1,3})\s*(?:din|dino|days?|दिन|दिनों)",
    re.IGNORECASE,
)


# v3.14 F1+F2 update: extended token set now covers "…baad" forms and the
# composer returns the FULL matched span (with any leading number/quantifier
# like "दो तीन हफ्ते"), not just the canonical label, so the confirm template
# doesn't drop the quantifier the customer actually said.
# v3.13: natural-language relative-date tokens. Trial-017 T? uttered
# "agle hafte tak shayad ho jayega" and the bot looped on "kab tak clear?"
# because no date parser recognized "agle hafte". These tokens now register a
# ledger date so the bucket router hits gives_date and confirms.
_RELATIVE_DATE_TOKENS: tuple[tuple[str, str], ...] = (
    # week-scale
    ("agle hafte", "agle hafte"),
    ("agle hafta", "agle hafte"),
    ("next week", "next week"),
    ("iss hafte", "iss hafte"),
    ("is hafte", "iss hafte"),
    ("this week", "this week"),
    ("hafte mein", "hafte mein"),
    ("hafta mein", "hafte mein"),
    ("hafte ke andar", "hafte ke andar"),
    ("hafte tak", "hafte tak"),
    ("hafte baad", "hafte baad"),
    ("hafte ke baad", "hafte ke baad"),
    ("week baad", "week baad"),
    ("week ke baad", "week ke baad"),
    ("weeks baad", "weeks baad"),
    ("week tak", "week tak"),
    ("week mein", "week mein"),
    ("week ke andar", "week ke andar"),
    ("aane wale hafte", "aane wale hafte"),
    ("aane waale hafte", "aane wale hafte"),
    ("do hafte", "do hafte"),
    ("do teen hafte", "do teen hafte"),
    ("teen hafte", "teen hafte"),
    ("chaar hafte", "chaar hafte"),
    ("char hafte", "char hafte"),
    ("अगले हफ्ते", "अगले हफ्ते"),
    ("अगले हफ़्ते", "अगले हफ्ते"),
    ("इस हफ्ते", "इस हफ्ते"),
    ("इस हफ़्ते", "इस हफ्ते"),
    ("हफ्ते तक", "हफ्ते तक"),
    ("हफ्ते में", "हफ्ते में"),
    # day-scale
    ("parson", "parson"),
    ("parso", "parson"),
    ("day after tomorrow", "day after tomorrow"),
    ("tomorrow", "kal"),
    ("kal tak", "kal tak"),
    ("kal shaam", "kal shaam"),
    ("kal subah", "kal subah"),
    ("kal", "kal"),
    ("aaj shaam", "aaj shaam"),
    ("aaj raat", "aaj raat"),
    ("aaj tak", "aaj tak"),
    ("today", "aaj"),
    ("परसों", "परसों"),
    ("कल तक", "कल तक"),
    ("कल शाम", "कल शाम"),
    ("कल सुबह", "कल सुबह"),
    ("कल", "कल"),
    ("आज शाम", "आज शाम"),
    ("आज तक", "आज तक"),
    # month-scale
    ("agle mahine", "agle mahine"),
    ("agle month", "agle month"),
    ("next month", "next month"),
    ("iss month", "iss month"),
    ("this month", "this month"),
    ("month end", "month end"),
    ("month ke baad", "month ke baad"),
    ("month baad", "month baad"),
    ("mahine baad", "mahine baad"),
    ("mahine ke baad", "mahine ke baad"),
    ("mahine ke end", "mahine ke end"),
    ("mahine tak", "mahine tak"),
    ("अगले महीने", "अगले महीने"),
    ("इस महीने", "इस महीने"),
    # salary/pay-day markers customers use as commitments
    ("salary aane par", "salary aane par"),
    ("salary aane ke baad", "salary aane ke baad"),
    ("salary ke baad", "salary ke baad"),
    ("salary aayegi", "salary aane par"),
    ("पगार आने पर", "पगार आने पर"),
    ("सैलरी के बाद", "सैलरी के बाद"),
)


_QUANTIFIER_PREFIX_ROMAN = re.compile(
    r"((?:do\s+teen|do|teen|char|chaar|paanch|panch|"
    r"\d{1,3})\s+)$",
    re.IGNORECASE,
)
_QUANTIFIER_PREFIX_DEVA = re.compile(
    r"((?:दो\s+तीन|दो|तीन|चार|पाँच|पांच|\d{1,3})\s+)$",
)


def parse_relative_date(text: str) -> str | None:
    """Return the customer's own phrasing of a relative-date commitment if
    the utterance contains one.

    v3.14 F2 change: instead of returning the canonical label (which caused
    trial-018 T4 to confirm "हफ्ते में" after the customer said "दो तीन
    हफ्ते में"), we return the matched token *with* any adjacent quantifier
    prefix ("do teen", "दो तीन", numerals). The confirm template then echoes
    what the customer actually said.
    """
    if not text:
        return None
    lower = text.lower()
    for tok, _canon in _RELATIVE_DATE_TOKENS:
        is_deva = any("\u0900" <= ch <= "\u097f" for ch in tok)
        haystack = text if is_deva else lower
        idx = haystack.find(tok)
        if idx < 0:
            continue
        # Look back for a quantifier just before the match.
        left = haystack[:idx]
        prefix_re = _QUANTIFIER_PREFIX_DEVA if is_deva else _QUANTIFIER_PREFIX_ROMAN
        m = prefix_re.search(left)
        if m:
            span = m.group(1) + tok
        else:
            span = tok
        # Trim any trailing whitespace and return in the original casing where
        # the token was matched (for Devanagari the raw text is authoritative).
        return span.strip()
    return None


def parse_day_offset(text: str) -> int | None:
    """Return N if the utterance says 'N din' / 'N days' / 'N दिन'.
    Supports Latin digits, Devanagari digits, Hindi-word counts, Hinglish
    Roman-word counts (e.g. 'pandrah din' → 15)."""
    if not text:
        return None
    normalized = text.translate(_DEVA_DIGIT)
    m = _DAY_UNIT_RE.search(normalized)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    # Hindi word + दिन
    for word, n in _HINDI_DAY_WORDS.items():
        if re.search(rf"{word}\s*दिन", text):
            return n
    # Hinglish word + din/days
    lower = normalized.lower()
    for word, n in _HINGLISH_DAY_WORDS.items():
        if re.search(rf"\b{word}\s+(?:din|days?)\b", lower):
            return n
    return None


# v3.9: sanitize residual bad fragments the LLM likes to emit even after guards.
#   * "आई हूँ" as bare fragment (missing verb) — replace with full clause.
#   * "कोई नहीं" (as consolation) — replace with "कोई बात नहीं".
_SANITIZE_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    # "कोई नहीं" only when used as consolation ("I understand, कोई नहीं" etc.)
    # NOT when part of "कोई नहीं है" (data statement). Match "कोई नहीं" at end of
    # a clause (before ।/,/./?/! or end-of-string).
    (re.compile(r"कोई\s+नहीं(?=\s*[।,.?!]|\s*$)"), "कोई बात नहीं"),
    # bare "आई हूँ" fragment — sits inside greeting-repair loop when LLM
    # truncates. Always paired with a purpose in Kavya's role.
    (re.compile(r"(?<![क-हa-zA-Z])आई हूँ(?![क-हa-zA-Z])"), "बात कर रही हूँ"),
    (re.compile(r"(?<![क-हa-zA-Z])आई हूं(?![क-हa-zA-Z])"), "बात कर रही हूँ"),
)


def sanitize_reply(reply: str) -> str:
    """Final purge of residual fragments after LLM + guards.
    Applied before ledger update + gather_say."""
    out = reply
    for pat, replacement in _SANITIZE_REPLACEMENTS:
        out = pat.sub(replacement, out)
    return out


def update_ledger(user_text: str, sess: dict) -> None:
    """Fix 7: ledger fields must ONLY be sourced from the customer utterance,
    never from Kavya's own reply. Trial-013 T2 wrote '30 July' into the ledger
    because ack_reply had fabricated it — this is the exact drift that guard
    now prevents. Amount/months come from the plan (Python-computed off the
    customer's stated per-month amount)."""
    c = sess["commitment"]
    # v3.12: reset per-turn flags so the bucket router sees only signals from
    # THIS utterance.
    c["_new_date_this_turn"] = False
    c["_new_amount_this_turn"] = False
    prev_date = c.get("date")
    prev_amount = c.get("amount")
    # v3.12: compute an EMI plan directly from the current utterance so the
    # bucket router can propose it without any LLM involvement.
    amt = parse_amount(user_text or "")
    if amt is not None and amt <= AMOUNT_MAX_PLAUSIBLE:
        cadence = amount_cadence(user_text or "")
        try:
            sess["_last_plan"] = compute_emi_hint(amt, cadence)
        except Exception:
            sess["_last_plan"] = None
    # commit any newly computed amount/months plan (plan itself was computed
    # from the customer's stated amount, so it's authoritative)
    plan = sess.get("_last_plan")
    if plan:
        if plan["kind"] == "monthly":
            c["amount"] = plan["amount"]
            c["months"] = plan["months"]
            c["cadence"] = "monthly"
        elif plan["kind"] in ("lumpsum_full", "lumpsum_partial"):
            c["amount"] = plan["amount"]
            c["cadence"] = "one-shot"
    # ONLY extract facts from customer speech, never from Kavya's reply.
    m = _DATE_LIKE_RE.search(user_text or "")
    if m:
        c["date"] = m.group(1).strip()
    # v3.11 fix B: N तारीख / N tareekh phrase.
    if not c.get("date"):
        tm = _TAREEKH_RE.search(user_text or "")
        if tm:
            c["date"] = f"{tm.group(1)} तारीख"
    # v3.9: relative-day promise (pandrah din / 15 din / पंद्रह दिन).
    days = parse_day_offset(user_text or "")
    if days and not c.get("date"):
        c["date"] = f"{days} din mein"
    # v3.13: natural-language relative dates ("agle hafte", "kal", "next
    # month", "salary ke baad" etc.) — trial-017 loop fix.
    if not c.get("date"):
        rel = parse_relative_date(user_text or "")
        if rel:
            c["date"] = rel
    # v3.12: set per-turn flags AFTER all writes so bucket router can tell
    # whether a fact appeared in THIS utterance vs an earlier one.
    if c.get("date") and c.get("date") != prev_date:
        c["_new_date_this_turn"] = True
    if c.get("amount") and c.get("amount") != prev_amount:
        c["_new_amount_this_turn"] = True


# ── Class 3 — Sir tail strip (retained lightly) ──────────────────────────────
_SIR_TAIL_PATTERNS = [
    re.compile(r",\s*sir\s*[।.!?]$", re.IGNORECASE),
    re.compile(r"\s+sir\s*[।.!?]$", re.IGNORECASE),
    re.compile(r",\s*sir\s*$", re.IGNORECASE),
    re.compile(r"\s+sir\s*$", re.IGNORECASE),
]


def strip_trailing_sir(reply: str) -> str:
    changed = reply
    for pat in _SIR_TAIL_PATTERNS:
        new = pat.sub("।" if changed.rstrip().endswith(("।", ".", "!", "?")) else "", changed)
        if new != changed:
            changed = new
            break
    # Cap "sir" mentions at one per reply.
    parts = re.split(r"(?i)(\bsir\b)", changed)
    seen = 0
    out: list[str] = []
    for p in parts:
        if p.lower() == "sir":
            seen += 1
            if seen > 1:
                continue
        out.append(p)
    changed = "".join(out)
    changed = re.sub(r"\s+", " ", changed).strip()
    changed = re.sub(r"\s+([।,.?!])", r"\1", changed)
    return changed or reply


def choose_fallback(user_text: str, sess: dict, category: str) -> str:
    intent = classify_intent(user_text)
    if intent == "identity":
        return TEMPLATE_ROUTES["identity"]
    if intent == "farewell":
        return TEMPLATE_ROUTES["farewell"]
    if intent == "hardship":
        return TEMPLATE_ROUTES["hardship"]
    if intent == "ack":
        return ack_reply(sess)
    if category == "slang":
        return "Ok, note कर लिया। और कुछ बताइए?"
    if category == "action_hallucination":
        return "Ok, मैं team से check करा के callback करा देती हूँ।"
    if category == "unsolicited_followup":
        return "Theek hai, note कर लिया।"
    if category == "masc_me":
        return "Theek hai, note कर रही हूँ।"
    if category == "hallucination":
        # LLM invented a fact (call recording, discount, waiver). Use the safe
        # authoritative-facts fallback so nothing outside CustomerContext is
        # ever spoken.
        return "आपके account पर fifty thousand outstanding है — कब तक clear हो जाएगा?"
    if category == "foreign_script":
        # LLM code-switched to Mandarin/Arabic. Reset to the same safe line.
        return "आपके account पर fifty thousand outstanding है — कब तक clear हो जाएगा?"
    return "Ok, note कर रही हूँ। और कुछ?"


def register_guard(reply: str, user_text: str, sess: dict) -> tuple[str, str]:
    """Return (final_reply, guard_action)."""
    hit = register_bad_hit(reply)
    if hit is None:
        return reply, "clean"
    fallback = choose_fallback(user_text, sess, hit)
    return fallback, f"replaced:{hit}"


# ── Rolling history ──────────────────────────────────────────────────────────
def rolling_history(full: list[dict]) -> list[dict]:
    if not full:
        return full
    system = [m for m in full if m["role"] == "system"][:1]
    tail = [m for m in full if m["role"] != "system"][-HISTORY_MAX_TURNS:]
    return system + tail


def call_llm(history: list[dict], hint: str | None,
             engine_prompt: str | None = None) -> tuple[str, dict]:
    trimmed = rolling_history(history)
    messages = list(trimmed)
    prepend: list[dict] = []
    if engine_prompt:
        # v3.9: PromptBuilder output — strategy, must-say, must-not-say, tone,
        # authoritative facts from the Volume 2 engine pipeline. Injected as a
        # dedicated system message so the LLM treats it as ground truth.
        prepend.append({"role": "system", "content": engine_prompt})
    if hint:
        # Inject after system, before rolling turns, so it steers the current reply.
        prepend.append({"role": "system", "content": hint})
    if prepend:
        messages = [messages[0]] + prepend + messages[1:]
    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": messages,
        "max_tokens": LLM_MAX_TOKENS,
        "temperature": 0.2,
        "frequency_penalty": 0.4,
        "presence_penalty": 0.2,
        "stop": ["\n\n", "\nUser:", "\nPrateek:", "\nCustomer:", "\nKavya:"],
    }).encode()
    req = urllib.request.Request(LLM_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    t0 = now_ms()
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_S) as r:
            raw = r.read()
    except Exception as e:
        t1 = now_ms()
        return "", {"llm_error": repr(e),
                    "llm_sent_ms": t0, "llm_done_ms": t1,
                    "llm_latency_ms": t1 - t0,
                    "history_len_sent": len(messages)}
    t1 = now_ms()
    data = json.loads(raw)
    text = data["choices"][0]["message"]["content"].strip()
    usage = data.get("usage") or {}
    return text, {
        "llm_sent_ms": t0, "llm_done_ms": t1,
        "llm_latency_ms": t1 - t0,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "history_len_sent": len(messages),
    }


# ── Class 3 — Simplified SSML ────────────────────────────────────────────────
# One <lang> wrapper per contiguous Latin run. No preceding <break>.
# No <s> tags. Let Polly's own sentence detection breathe on ।/./?/!.
_LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z0-9'’./&-]*(?:\s+[A-Za-z][A-Za-z0-9'’./&-]*)*")


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&apos;"))


def _normalize_punct(text: str) -> str:
    """Reduce surprise Polly pauses: collapse multiple punctuation, drop stray commas."""
    text = re.sub(r"—+", ",", text)          # em-dash → comma (short pause, not long)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"([।.!?]){2,}", r"\1", text)
    text = re.sub(r"[,]{2,}", ",", text)
    text = re.sub(r"\s+([।,.?!])", r"\1", text)
    return text.strip()


# Fix 11: emphasize amount/date fragments so customer catches them.
_EMPHASIS_AMOUNT_PATTERNS = [
    (re.compile(r"\bfifty thousand rupees\b", re.IGNORECASE), "fifty thousand rupees"),
    (re.compile(r"\bfifty thousand\b", re.IGNORECASE), "fifty thousand"),
    (re.compile(r"\bpachaas hazaar\b", re.IGNORECASE), "pachaas hazaar"),
    (re.compile(r"\b50[,\s]?000\b"), "pachaas hazaar rupay"),
    (re.compile(r"\bRs\.?\s*50[,\s]?000\b", re.IGNORECASE), "pachaas hazaar rupay"),
    (re.compile(r"\b50000\b"), "pachaas hazaar rupay"),
]
_EMPHASIS_DATE_RE = re.compile(
    r"\b(\d{1,2}\s*(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December|जनवरी|फ़रवरी|मार्च|अप्रैल|मई|जून|"
    r"जुलाई|अगस्त|सितंबर|अक्टूबर|नवंबर|दिसंबर))\b",
    re.IGNORECASE,
)

# Marker tokens used to insert SSML tags without them being consumed by the
# Latin-run detector. We substitute markers first, then run the Latin wrapper,
# then swap markers back to real SSML at the end.
_EMPH_OPEN = "\uE000"
_EMPH_CLOSE = "\uE001"
_BREAK_150 = "\uE002"


def _mark_emphasis(text: str) -> str:
    for pat, replacement in _EMPHASIS_AMOUNT_PATTERNS:
        text = pat.sub(f"{_EMPH_OPEN}{replacement}{_EMPH_CLOSE}", text)
    text = _EMPHASIS_DATE_RE.sub(f"{_EMPH_OPEN}\\1{_EMPH_CLOSE}", text)
    return text


def _mark_devanagari_break(text: str) -> str:
    """Fix 12: micro-pause after Devanagari sentence-end (danda). Not after
    Latin `.` — Polly's own sentence detection handles Latin fine."""
    return text.replace("।", f"।{_BREAK_150}")


def _finalize_markers(text: str) -> str:
    text = text.replace(_EMPH_OPEN, '<emphasis level="moderate">')
    text = text.replace(_EMPH_CLOSE, '</emphasis>')
    text = text.replace(_BREAK_150, '<break time="150ms"/>')
    return text


def prepare_ssml(text: str) -> str:
    text = _normalize_punct(text.replace("\r", " ").replace("\n", " "))
    text = _mark_emphasis(text)
    text = _mark_devanagari_break(text)
    parts: list[str] = []
    last = 0
    for m in _LATIN_RUN.finditer(text):
        if m.start() > last:
            parts.append(_xml_escape(text[last:m.start()]))
        parts.append(f'<lang xml:lang="en-IN">{_xml_escape(m.group(0))}</lang>')
        last = m.end()
    if last < len(text):
        parts.append(_xml_escape(text[last:]))
    out = "".join(parts)
    return _finalize_markers(out)


# ── v3.9 engine pipeline ────────────────────────────────────────────────────

def _build_turn_input(call_sid: str, sess: dict, speech: str,
                       confidence: float) -> TurnInput:
    """Convert a Twilio webhook turn into the canonical TurnInput contract."""
    now = datetime.now(timezone.utc)
    turn_idx = sess.get("turn", 0)
    return TurnInput(
        turn_id=f"{call_sid}-t{turn_idx}",
        call_id=call_sid,
        tenant_id=TenantId("founder-demo"),
        role=TurnRole.CUSTOMER,
        transcript=speech,
        segments=(
            UtteranceSegment(
                text=speech, start_ms=0, end_ms=max(1, len(speech) * 60),
                confidence=max(0.0, min(1.0, confidence)),
            ),
        ),
        created_at=now,
        correlation_id=f"{call_sid}-c{turn_idx}",
        trace_id=f"{call_sid}-tr{turn_idx}",
        turn_index=turn_idx,
    )


def _run_engine_pipeline(turn: TurnInput, sess: dict) -> dict[str, Any]:
    """Call every VoiceOS engine for this turn.

    Returns a dict of engine outputs:
      response_plan, decision_envelope, empathy_config, voice_config,
      prompt_text, prompt_hash, adaptive_signal, quality_score (fire-and-forget).
    Errors from any engine are caught, logged, and result-null so the webhook
    stays live even if an engine misbehaves in production.
    """
    outputs: dict[str, Any] = {}
    try:
        response_plan, decision_envelope = RESPONSE_PLANNING.assemble(
            turn=turn,
            context=CUSTOMER_CONTEXT,
            retrieval=[],
            intent_history=list(sess.get("intent_history", []))[-8:],
            identity_verified=bool(sess.get("identity_verified", False)),
            silence_duration_ms=0,
        )
        outputs["response_plan"] = response_plan
        outputs["decision_envelope"] = decision_envelope
    except Exception as e:
        log.warning("ResponsePlanningEngine failure: %s", e)
        outputs["response_plan"] = None
        outputs["decision_envelope"] = None

    plan = outputs.get("response_plan")
    emp: EmpathyConfig | None = None
    vc: VoiceConfig | None = None
    if plan is not None:
        try:
            emo = plan.emotion
            stress = StressLevel(emo.stress_level) if isinstance(
                emo.stress_level, str) else emo.stress_level
            sentiment = Sentiment(emo.sentiment) if isinstance(
                emo.sentiment, str) else emo.sentiment
        except Exception:
            stress = StressLevel.LOW
            sentiment = Sentiment.NEUTRAL
        try:
            emp = EMPATHY_PLANNER.plan(
                stress_level=stress, sentiment=sentiment,
                preferred_language="hi-IN",
            )
            outputs["empathy_config"] = emp
        except Exception as e:
            log.warning("EmpathyPlanner failure: %s", e)
        try:
            if emp is not None:
                vc = PROSODY_ENGINE.translate(
                    empathy_config=emp,
                    language="hi-IN",
                    turn_index=int(sess.get("turn", 0)),
                )
                outputs["voice_config"] = vc
        except Exception as e:
            log.warning("AdaptiveProsodyEngine failure: %s", e)

    try:
        adaptive = ADAPTIVE_ENGINE.process(
            intent_history=list(sess.get("intent_history", []))[-8:],
            silence_duration_ms=0,
        )
        outputs["adaptive_signal"] = adaptive
    except Exception as e:
        log.warning("AdaptiveConversationEngine failure: %s", e)

    try:
        if plan is not None:
            prompt_text, prompt_hash = PROMPT_BUILDER.build(
                response_plan=plan, context=CUSTOMER_CONTEXT,
            )
            outputs["prompt_text"] = prompt_text
            outputs["prompt_hash"] = prompt_hash
    except Exception as e:
        log.warning("PromptBuilder failure: %s", e)

    if vc is not None:
        sess["voice_config"] = vc

    if plan is not None:
        try:
            primary_intent = plan.intents[0].label if plan.intents else None
            if primary_intent is not None:
                sess["intent_history"].append(primary_intent)
                sess["intent_history"] = sess["intent_history"][-16:]
        except Exception:
            pass

    return outputs


def _score_output_async(turn: TurnInput, llm_output: str, plan) -> None:
    """Fire-and-forget OutputEvaluationEngine.score — never blocks TTS."""
    try:
        score = OUTPUT_EVALUATOR.score(
            turn=turn, llm_output=llm_output, response_plan=plan,
        )
        log.info("QUALITY | turn=%s coh=%.2f pol=%.2f emp=%.2f fact=%.2f",
                 score.turn_id, score.coherence, score.policy_compliance,
                 score.empathy, score.factual_accuracy)
    except Exception as e:
        log.debug("OutputEvaluationEngine skipped: %s", e)


# ── v3.11 conversation state machine ────────────────────────────────────────

# v3.14 F3: broad bare-affirmation matcher used only after a confirm ask,
# so we don't accidentally close on generic "haan" mid-conversation.
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


def _classify_bucket(speech: str, sess: dict, intent: str) -> str:
    """v3.12: bucket router — every post-identity turn maps to exactly one
    of the buckets in the accepted design tree. No LLM in the golden path.

    Returns one of: ask_who, ask_amount, hardship, gives_date, gives_amount,
    ack, farewell, loan_denial, else.
    """
    if intent == "farewell":
        return "farewell"
    if intent == "loan_denial":
        return "loan_denial"
    if intent in ("identity", "why_called"):
        return "ask_who"
    if intent == "amount_query":
        return "ask_amount"
    # v3.13: a concrete date/amount commitment in THIS utterance beats a
    # co-occurring hardship marker. Trial-017 T2 said "अभी पैसे नहीं, अगले
    # हफ्ते तक payment" — hardship fired first and the date got dropped,
    # producing the loop. Give commitments priority when they carry a date.
    c = sess.get("commitment", {})
    if c.get("_new_date_this_turn"):
        return "gives_date"
    if c.get("_new_amount_this_turn"):
        return "gives_amount"
    if intent == "hardship":
        return "hardship"
    if intent == "ack":
        return "ack"
    # v3.14 F3: bare affirmations after we've asked for a confirmation must
    # close the call — not fall through to `else` and get re-anchored. Trial-
    # 018 T5 said "यस" after Kavya's confirm_date and the intent classifier
    # tagged it `open`, so it hit `anchor` and produced a duplicate-fallback
    # grammar leak ("हफ्ते में तक payment note है — और कुछ बताइए?"). Widen
    # the ack detector for the confirm-state specifically.
    last_ask = sess.get("last_ask", "")
    if last_ask in ("confirm_date", "confirm_plan"):
        norm = (speech or "").strip().lower()
        if _AFFIRM_AFTER_CONFIRM_RE.search(norm) or \
           _AFFIRM_DEVA_RE.search(speech or ""):
            return "ack"
    return "else"


def _run_state_machine(sess: dict, speech: str, intent: str) -> tuple[str, str] | None:
    """v3.12: fully deterministic bucket router. Every turn maps to a
    scripted reply — no LLM fallback for the golden path.

    States:
      * AWAIT_IDENTITY  — turn 1 identity gate
      * CONVERSATION    — deterministic bucket router
      * CLOSE           — farewell / hangup path
    """
    state = sess.get("state_name", "AWAIT_IDENTITY")
    sess["state_turn"] = int(sess.get("state_turn", 0)) + 1

    # ── AWAIT_IDENTITY ────────────────────────────────────────────────────
    if state == "AWAIT_IDENTITY":
        verdict = classify_identity_response(speech)
        if verdict == "confirm":
            sess["identity_verified"] = True
            sess["state_name"] = "CONVERSATION"
            sess["state_turn"] = 0
            sess["last_ask"] = "when_pay"
            return SCRIPT_TEMPLATES["identity_confirm"], "state:AWAIT_IDENTITY:confirm"
        if verdict == "deny":
            sess["state_name"] = "CLOSE"
            sess["farewell_requested"] = True
            return SCRIPT_TEMPLATES["identity_deny"], "state:AWAIT_IDENTITY:deny"
        if not sess.get("identity_reprompted"):
            sess["identity_reprompted"] = True
            return SCRIPT_TEMPLATES["identity_reask"], "state:AWAIT_IDENTITY:reask"
        sess["state_name"] = "CONVERSATION"
        sess["state_turn"] = 0
        sess["last_ask"] = "when_pay"
        return SCRIPT_TEMPLATES["identity_confirm"], "state:AWAIT_IDENTITY:proceed"

    # ── CLOSE ─────────────────────────────────────────────────────────────
    if state == "CLOSE":
        return SCRIPT_TEMPLATES["close_farewell"], "state:CLOSE:repeat"

    # ── CONVERSATION — bucket router ──────────────────────────────────────
    bucket = _classify_bucket(speech, sess, intent)
    c = sess.get("commitment", {})

    if bucket == "farewell":
        sess["state_name"] = "CLOSE"
        return SCRIPT_TEMPLATES["close_farewell"], "state:CONV:farewell"

    if bucket == "loan_denial":
        sess["state_name"] = "CLOSE"
        return SCRIPT_TEMPLATES["close_callback"], "state:CONV:loan_denial"

    if bucket == "ask_who":
        sess["last_ask"] = "when_pay"
        return (SCRIPT_TEMPLATES["ask_who"] + " कब तक payment हो जाएगा sir?",
                "state:CONV:ask_who")

    if bucket == "ask_amount":
        sess["last_ask"] = "when_pay"
        return SCRIPT_TEMPLATES["ask_amount"], "state:CONV:ask_amount"

    if bucket == "hardship":
        sess["last_ask"] = "part_payment"
        return SCRIPT_TEMPLATES["hardship"], "state:CONV:hardship"

    if bucket == "gives_date":
        sess["last_ask"] = "confirm_date"
        date_str = c.get("date") or ""
        # v3.12: "15 din mein" already contains a postposition — using "तक"
        # after it produces "15 din mein तक" (ungrammatical). Pick a relative
        # variant for phrases that already carry a postposition/terminator;
        # use the तक form for bare absolute or bare-relative dates.
        _already_terminated = (
            "mein" in date_str or "में" in date_str
            or "tak" in date_str.lower() or "तक" in date_str
            or "andar" in date_str.lower() or "अंदर" in date_str
            or "par" in date_str.lower() or "पर" in date_str
            or "baad" in date_str.lower() or "बाद" in date_str
            or "end" in date_str.lower()
            or "shaam" in date_str.lower() or "शाम" in date_str
            or "subah" in date_str.lower() or "सुबह" in date_str
            or "raat" in date_str.lower() or "रात" in date_str
        )
        if _already_terminated:
            tpl = SCRIPT_TEMPLATES["gives_date_confirm_relative"]
        else:
            tpl = SCRIPT_TEMPLATES["gives_date_confirm"]
        return (tpl.format(date=date_str), "state:CONV:gives_date")

    if bucket == "gives_amount":
        amt = c.get("amount") or 0
        plan = sess.get("_last_plan") or {}
        months = plan.get("months")
        sess["last_ask"] = "confirm_plan"
        if months:
            return (SCRIPT_TEMPLATES["plan_computed"].format(
                        amount=amt, months=months),
                    "state:CONV:plan_computed")
        return (SCRIPT_TEMPLATES["gives_amount_plan"].format(amount=amt),
                "state:CONV:gives_amount")

    if bucket == "ack":
        # Ack advances based on what we last asked.
        last = sess.get("last_ask", "")
        if last in ("confirm_date", "confirm_plan"):
            sess["state_name"] = "CLOSE"
            return SCRIPT_TEMPLATES["close_soft"], "state:CONV:ack_close"
        # Bare ack early — re-anchor politely.
        sess["last_ask"] = "when_pay"
        return SCRIPT_TEMPLATES["anchor"], "state:CONV:ack_anchor"

    # else — anything unmatched → anchor back to the primary ask.
    sess["last_ask"] = "when_pay"
    return SCRIPT_TEMPLATES["anchor"], "state:CONV:anchor"


def _rate_percent(voice_config: VoiceConfig | None) -> str:
    """Convert VoiceConfig.rate_scale → Polly rate percentage (e.g. '+5%').
    Neural voices accept rate as % or absolute. Clamp to [80%, 115%] so no
    reply sounds bizarrely fast or slow."""
    if voice_config is None:
        return "+8%"
    scale = float(voice_config.rate_scale)
    scale = max(0.80, min(1.15, scale))
    pct = int(round((scale - 1.0) * 100))
    return f"{pct:+d}%"


def _volume_db(voice_config: VoiceConfig | None) -> str:
    """Convert VoiceConfig.energy_scale → Polly volume dB (e.g. '+2dB')."""
    if voice_config is None:
        return "+0dB"
    scale = float(voice_config.energy_scale)
    scale = max(0.85, min(1.15, scale))
    if scale >= 1.0:
        db = int(round((scale - 1.0) * 20))
    else:
        db = -int(round((1.0 - scale) * 20))
    return f"{db:+d}dB"


def say_tag(text: str, rate: str | None = None, pitch: str | None = None,
             voice_config: VoiceConfig | None = None) -> str:
    """Emit a <Say> with per-turn prosody driven by AdaptiveProsodyEngine.

    Notes on Kajal-Neural (v3.9):
      * Neural voices do NOT support <emphasis> or <prosody pitch>.
      * We strip both when finalizing markers for Neural voice output.
      * Rate + volume come from VoiceConfig; pause markers stay (Neural OK).
    """
    body = prepare_ssml(text)
    is_neural = "neural" in POLLY_VOICE.lower()
    if is_neural:
        # Strip pitch/emphasis tags that Kajal-Neural rejects.
        body = re.sub(r'</?emphasis[^>]*>', '', body)
    r = rate or _rate_percent(voice_config)
    vol = _volume_db(voice_config)
    prosody_open = f'<prosody rate="{r}" volume="{vol}">'
    if not is_neural:
        p = pitch or POLLY_PITCH
        prosody_open = f'<prosody rate="{r}" pitch="{p}" volume="{vol}">'
    return (f'<Say language="{POLLY_VOICE_LANGUAGE}" voice="{POLLY_VOICE}">'
            f'{prosody_open}{body}</prosody></Say>')


def gather_say(text: str, timeout_s: int = 12, rate: str | None = None,
               pitch: str | None = None,
               voice_config: VoiceConfig | None = None) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'<Gather input="speech dtmf" language="hi-IN" speechTimeout="{GATHER_SPEECH_TIMEOUT}" '
        f'timeout="{timeout_s}" numDigits="1" action="/respond" method="POST">'
        f'{say_tag(text, rate=rate, pitch=pitch, voice_config=voice_config)}'
        '</Gather>'
        '<Redirect method="POST">/timeout</Redirect>'
        '</Response>'
    )


def gather_only(timeout_s: int = 12) -> str:
    """TwiML that opens a listening Gather WITHOUT any nested <Say>.
    Used by /listen after an uninterruptible greeting has already played."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'<Gather input="speech dtmf" language="hi-IN" speechTimeout="{GATHER_SPEECH_TIMEOUT}" '
        f'timeout="{timeout_s}" numDigits="1" action="/respond" method="POST"/>'
        '<Redirect method="POST">/timeout</Redirect>'
        '</Response>'
    )


# ── Endpoints ────────────────────────────────────────────────────────────────
@app.get("/static/greeting.mp3")
async def greeting_mp3():
    return FileResponse(GREETING_MP3_PATH, media_type="audio/mpeg",
                        filename="greeting.mp3")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "3.14"}


@app.get("/health/deep")
async def health_deep():
    checks: dict[str, Any] = {"self": "ok", "version": "3.14"}
    try:
        req = urllib.request.Request(
            LLM_URL.rsplit("/chat/completions", 1)[0] + "/models")
        with urllib.request.urlopen(req, timeout=3) as r:
            models = json.loads(r.read()).get("data", [])
            checks["llm"] = "ok" if models else "empty"
    except Exception as e:
        checks["llm"] = f"error:{e}"
    checks["sessions_active"] = len(sessions)
    checks["polly_rate"] = POLLY_RATE
    checks["polly_pitch"] = POLLY_PITCH
    checks["polly_greeting_rate"] = POLLY_GREETING_RATE
    checks["polly_greeting_pitch"] = POLLY_GREETING_PITCH
    checks["history_max_turns"] = HISTORY_MAX_TURNS
    checks["llm_max_tokens"] = LLM_MAX_TOKENS
    return checks


@app.api_route("/welcome", methods=["GET", "POST"])
async def welcome(request: Request):
    """v3.9 uninterruptible greeting: <Say> lives OUTSIDE any <Gather> so
    Twilio does not begin listening until the greeting completes. Then we
    <Redirect> to /listen which opens the actual Gather for user speech.
    """
    t_recv = now_ms()
    form = await request.form()
    call_sid = form.get("CallSid") or f"nosid-{uuid.uuid4()}"
    sess = get_session(call_sid)
    sess["history"].append({"role": "assistant", "content": GREETING_TEXT})
    sess["meta"] = {k: form.get(k) for k in
                    ("From", "To", "CallStatus", "Direction", "AccountSid")
                    if form.get(k)}
    _clear_timeout(call_sid)
    # Seed a warm greeting VoiceConfig: friendly + normal pacing.
    greet_emp = EMPATHY_PLANNER.plan(
        stress_level=StressLevel.LOW,
        sentiment=Sentiment.POSITIVE,
        preferred_language="hi-IN",
    )
    greet_vc = PROSODY_ENGINE.translate(
        empathy_config=greet_emp, language="hi-IN", turn_index=0,
    )
    # v3.11 fix F: Delhi-style greeting must sound uninterrupted AND a touch
    # faster than normal. VoiceConfig is Pydantic-frozen, so rebuild it with
    # rate_scale forced to 1.08 (≈ +8% over baseline). Say-outside-Gather
    # already guarantees the "uninterrupted" half.
    try:
        greet_vc = greet_vc.model_copy(update={"rate_scale": 1.08})
    except Exception:
        pass
    sess["voice_config"] = greet_vc
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        '<Pause length="1"/>'
        f'{say_tag(GREETING_TEXT, voice_config=greet_vc)}'
        '<Redirect method="POST">/listen</Redirect>'
        '</Response>'
    )
    t_ret = now_ms()
    turn_log({
        "event": "welcome",
        "call_sid": call_sid,
        "meta": sess["meta"],
        "webhook_received_ms": t_recv,
        "twiml_returned_ms": t_ret,
        "handler_latency_ms": t_ret - t_recv,
        "greeting_text": GREETING_TEXT,
        "greeting_voice_rate_scale": float(greet_vc.rate_scale),
        # v3.14 F13: latency slot for the /welcome greeting. TwiML render
        # completes at t_ret; the audible first-word arrives when Twilio
        # fetches the Kajal-Neural audio (external, not measurable here).
        # We log the handler slice so the founder audit trail is honest
        # about what we measure vs. what Twilio measures.
        "welcome_render_latency_ms": t_ret - t_recv,
    })
    return Response(content=twiml, media_type="text/xml")


@app.api_route("/listen", methods=["GET", "POST"])
async def listen(request: Request):
    """Opened after uninterruptible greeting completes: pure Gather, no Say.
    Redirects to /timeout if the caller stays silent."""
    twiml = gather_only(timeout_s=12)
    return Response(content=twiml, media_type="text/xml")


@app.api_route("/respond", methods=["GET", "POST"])
async def respond(request: Request):
    t_recv = now_ms()
    form = await request.form()
    call_sid = form.get("CallSid") or "nosid"
    speech = (form.get("SpeechResult") or "").strip()
    digits = (form.get("Digits") or "").strip()
    # Fix 5: never use `and confidence` as truthiness — 0.0 is a real, valid
    # confidence value (STT thinks the utterance is garbage). Coerce the empty
    # string case defensively via `or "1.0"`, then compare as `<`.
    try:
        confidence = float(form.get("Confidence", "1.0") or "1.0")
    except ValueError:
        confidence = 0.0

    sess = get_session(call_sid)
    sess["turn"] += 1
    turn = sess["turn"]

    if not speech and digits:
        speech = "Haan boliye"

    # -- empty utterance --
    if not speech:
        twiml = gather_say(
            "क्या आपने कुछ कहा? मुझे सुनाई नहीं दिया, please दोबारा बोलिए।",
            timeout_s=10, voice_config=sess.get("voice_config"))
        t_ret = now_ms()
        turn_log({
            "event": "respond_empty",
            "call_sid": call_sid, "turn": turn,
            "webhook_received_ms": t_recv,
            "twiml_returned_ms": t_ret,
            "handler_latency_ms": t_ret - t_recv,
            "speech_result": speech, "digits": digits,
            "speech_confidence": confidence,
        })
        return Response(content=twiml, media_type="text/xml")

    sess["history"].append({"role": "user", "content": speech})

    # v3.9: run the full Volume 2 engine pipeline BEFORE routing so intent,
    # emotion, strategy, negotiation envelope, empathy + prosody are computed
    # for every turn — even template routes (voice_config still applies).
    turn_input = _build_turn_input(call_sid, sess, speech, confidence)
    engine_out = _run_engine_pipeline(turn_input, sess)
    response_plan = engine_out.get("response_plan")
    engine_prompt = engine_out.get("prompt_text")
    voice_config = engine_out.get("voice_config") or sess.get("voice_config")

    # -- Class 4 (intent classification) + Class 1 (template routing) --
    intent = classify_intent(speech)
    guard_action = "n/a"
    llm_timing: dict[str, Any] = {}
    raw_reply = ""

    # v3.12: update the ledger FIRST from the customer utterance so the
    # bucket router sees whether a date / amount arrived in THIS turn.
    update_ledger(speech, sess)
    _ledger_updated_early = True

    # v3.11 fix A: run deterministic state machine FIRST. If it returns a
    # (reply, route) tuple, the script owns the turn — LLM is bypassed. Only
    # None falls through to the classic intent/template/LLM router.
    sm_result = _run_state_machine(sess, speech, intent)
    if sm_result is not None:
        reply, route = sm_result
    elif confidence < CONFIDENCE_LOW:
        # Fix 5: fire even at 0.0. Low-confidence STT — clarify, do not call LLM.
        reply = TEMPLATE_ROUTES["low_confidence"]
        route = "low_confidence"
    elif intent in TEMPLATE_ROUTES and intent not in ("empty",):
        reply = TEMPLATE_ROUTES[intent]
        route = f"template:{intent}"
    elif intent == "ack":
        reply = ack_reply(sess)
        route = "template:ack"
    elif (response_plan is not None and response_plan.intents
          and str(response_plan.intents[0].label) in
              ("IntentLabel.HARDSHIP", "HARDSHIP")):
        # v3.9 defect-2: engine-detected HARDSHIP overrides LLM. Deterministic
        # empathetic acknowledgement + concrete relief proposal — no drift.
        reply = TEMPLATE_ROUTES["hardship"]
        route = "engine:hardship"
    else:
        route = "llm"
        # v3.11 fix G: after 2 hallucination hits in this call, drop LLM route
        # entirely and fall back to the safest deterministic answer for the
        # rest of the call. Anchor: authoritative outstanding + ask commitment.
        if int(sess.get("hallucination_hits", 0)) >= 2:
            reply = TEMPLATE_ROUTES.get("amount_query",
                "आपके account पर fifty thousand outstanding है sir।")
            route = "hallucination_lock"
        # Amount plausibility gate: if user text has garbled amount, force clarify.
        elif (amt := parse_amount(speech)) is not None and amt > AMOUNT_MAX_PLAUSIBLE:
            reply = TEMPLATE_ROUTES["garbled_amount"]
            route = "template:garbled_amount"
        else:
            hint = build_assistant_hint(speech, sess, confidence)
            raw_reply, llm_timing = call_llm(
                sess["history"], hint, engine_prompt=engine_prompt,
            )
            if not raw_reply:
                reply = "माफ़ कीजिए, ज़रा दोबारा बोलेंगे?"
                route = "llm_error"
            else:
                # (a) name scrub
                reply = dedupe_name(raw_reply, sess)
                # (b) register guard — reject-and-replace whole reply if bad
                reply, guard_action = register_guard(reply, speech, sess)
                # v3.11 fix G: bump hallucination counter on serious hits so
                # the /respond router can lock LLM after 2 strikes.
                if guard_action.startswith("replaced:") and any(
                    tag in guard_action for tag in
                    ("hallucination", "action_hallucination", "unsolicited_followup")
                ):
                    sess["hallucination_hits"] = int(
                        sess.get("hallucination_hits", 0)) + 1
                # (c) number reconciliation vs Python-computed plan
                reply = reconcile_months(reply, sess.get("_last_plan"))
                # (d) Fix 2: authoritative-amount guard — replace whole reply
                #     if it mentions an amount not on file.
                reply, amount_action = guard_reply_amounts(
                    reply, sess.get("_last_plan"))
                if amount_action != "amount_ok":
                    guard_action = (guard_action + "+" + amount_action
                                    if guard_action != "clean" else amount_action)
                # (d2) Fix 1: date guard — replace whole reply if it confirms
                #      a date the customer never uttered and isn't on ledger.
                reply, date_action = guard_reply_date(reply, speech, sess)
                if date_action != "date_ok":
                    guard_action = (guard_action + "+" + date_action
                                    if guard_action not in ("clean", "n/a")
                                    else date_action)
                # (e) tail-sir strip
                reply = strip_trailing_sir(reply)

    # Fix 7: ledger update takes CUSTOMER speech, never Kavya's reply.
    # v3.12: skipped if we already updated it before the state machine.
    if not locals().get("_ledger_updated_early"):
        update_ledger(speech, sess)

    # Fix (dedupe): if reply already said, choose a contextual next-step
    # instead of falling back to a fixed string that itself becomes duplicate.
    prior = {p.strip() for p in sess["assistant_replies"]}
    if reply.strip() in prior:
        c = sess["commitment"]
        if c["amount"] and c["months"]:
            reply = f"Toh {c['amount']} monthly पर {c['months']} महीने का plan set है, ठीक?"
        elif c["date"]:
            # v3.14 F4: don't append "तक" when the date phrase already carries
            # its own postposition/terminator ("हफ्ते में", "salary aane par",
            # "kal शाम", "month end"). Trial-018 T5 printed "हफ्ते में तक"
            # because we blindly appended "तक". Reuse the terminator check.
            _dt = c["date"]
            _dt_low = _dt.lower()
            _terminated = (
                "mein" in _dt_low or "में" in _dt
                or "tak" in _dt_low or "तक" in _dt
                or "andar" in _dt_low or "अंदर" in _dt
                or "par" in _dt_low or "पर" in _dt
                or "baad" in _dt_low or "बाद" in _dt
                or "end" in _dt_low
                or "shaam" in _dt_low or "शाम" in _dt
                or "subah" in _dt_low or "सुबह" in _dt
                or "raat" in _dt_low or "रात" in _dt
            )
            reply = (f"{_dt} payment note कर लिया sir।"
                     if _terminated
                     else f"{_dt} तक payment note कर लिया sir।")
        else:
            reply = "आपके account पर fifty thousand outstanding है — कब तक clear हो जाएगा?"

    # v3.9: final sanitize pass — purge "आई हूँ" fragment, replace "कोई नहीं".
    reply = sanitize_reply(reply)

    # v3.14: apply EmpathyDirective on top of the (scripted or LLM-authored)
    # reply. Adds the SSML listening break + bilingual acknowledgment prefix
    # and folds a prosody dip into VoiceConfig so hardship states sound
    # visibly slower + softer, not just semantically empathetic.
    try:
        # Only apply on CONVERSATION-state replies; AWAIT_IDENTITY and CLOSE
        # already have hand-tuned language and don't need lexical warmth.
        _bucket_hint = ""
        if sm_result is not None:
            _bucket_hint = sm_result[1].split(":")[-1] if sm_result[1] else ""
        _in_conv = sess.get("state_name") in ("CONVERSATION",)
        if _in_conv:
            _directive = EMPATHY_COMPOSER.compose(speech, _bucket_hint)
            reply = EMPATHY_COMPOSER.apply_to_reply(reply, _directive)
            # Fold prosody dip into VoiceConfig (bounded so we never invert
            # sign or fall below the AdaptiveProsody clamps).
            if voice_config is not None and _directive.state != EmpathyState.NEUTRAL:
                _new_rate = max(0.80, min(1.15,
                    float(voice_config.rate_scale) + _directive.rate_scale_delta))
                _new_energy = max(0.85, min(1.15,
                    float(voice_config.energy_scale) + _directive.energy_scale_delta))
                try:
                    voice_config = voice_config.model_copy(update={
                        "rate_scale":  _new_rate,
                        "energy_scale": _new_energy,
                    })
                    sess["voice_config"] = voice_config
                except Exception:
                    pass
            sess["_empathy_state"] = _directive.state.value
    except Exception as e:
        log.warning("EmpathyDirective apply failed: %s", e)

    sess["assistant_replies"].append(reply)
    sess["history"].append({"role": "assistant", "content": reply})

    # v3.9: fire-and-forget OutputEvaluationEngine.score() — never blocks TTS.
    if response_plan is not None:
        try:
            threading.Thread(
                target=_score_output_async,
                args=(turn_input, reply, response_plan),
                daemon=True,
            ).start()
        except Exception as e:
            log.debug("output evaluation dispatch skipped: %s", e)

    twiml = gather_say(reply, voice_config=voice_config)
    t_ret = now_ms()
    # Extras: single-line TURN log for instant debugging.
    log.info("TURN | call=%s turn=%d conf=%.2f intent=%s route=%s guard=%s | "
             "customer=%r | kavya=%r",
             call_sid, turn, confidence, intent, route, guard_action,
             speech, reply)
    turn_log({
        "event": "turn",
        "call_sid": call_sid, "turn": turn,
        "webhook_received_ms": t_recv,
        "llm_sent_ms": llm_timing.get("llm_sent_ms"),
        "llm_done_ms": llm_timing.get("llm_done_ms"),
        "twiml_returned_ms": t_ret,
        "handler_latency_ms": t_ret - t_recv,
        "llm_latency_ms": llm_timing.get("llm_latency_ms"),
        "prompt_tokens": llm_timing.get("prompt_tokens"),
        "completion_tokens": llm_timing.get("completion_tokens"),
        "history_len_sent": llm_timing.get("history_len_sent"),
        "llm_error": llm_timing.get("llm_error"),
        "speech_result": speech,
        "speech_confidence": confidence,
        "digits": digits,
        "intent": intent,
        "route": route,
        "guard_action": guard_action,
        "commitment": sess["commitment"],
        "llm_reply_raw": raw_reply,
        "llm_reply": reply,
        # v3.9: engine pipeline observability
        "engine": {
            "primary_intent": (
                response_plan.intents[0].label if response_plan
                and response_plan.intents else None
            ),
            "strategy": (
                response_plan.strategy.action if response_plan else None
            ),
            "goal": response_plan.goal if response_plan else None,
            "prompt_hash": engine_out.get("prompt_hash"),
            "voice_rate_scale": (
                float(voice_config.rate_scale) if voice_config else None
            ),
            "voice_energy_scale": (
                float(voice_config.energy_scale) if voice_config else None
            ),
            "prompt_injected": engine_prompt is not None,
        },
    })
    return Response(content=twiml, media_type="text/xml")


# Fix 8: graduated timeout response — three tries before hang-up, so a caller
# with a bad line doesn't hear the same static goodbye on every silence.
_timeout_counts_lock = threading.Lock()
_timeout_counts: dict[str, int] = {}


def _bump_timeout(call_sid: str) -> int:
    with _timeout_counts_lock:
        _timeout_counts[call_sid] = _timeout_counts.get(call_sid, 0) + 1
        return _timeout_counts[call_sid]


def _clear_timeout(call_sid: str) -> None:
    with _timeout_counts_lock:
        _timeout_counts.pop(call_sid, None)


_TIMEOUT_LINES = {
    1: "क्या आपको मेरी आवाज़ आ रही है? Please दोबारा बोलिए।",
    2: "मुझे सुनाई नहीं दे रहा। एक बार और try कीजिए।",
}


@app.api_route("/timeout", methods=["GET", "POST"])
async def timeout(request: Request):
    t_recv = now_ms()
    form = await request.form()
    call_sid = form.get("CallSid") or "nosid"
    count = _bump_timeout(call_sid)
    turn_log({
        "event": "timeout",
        "call_sid": call_sid,
        "timeout_count": count,
        "webhook_received_ms": t_recv,
        "call_status": form.get("CallStatus"),
    })
    if count <= 2:
        line = _TIMEOUT_LINES[count]
        twiml = gather_say(line, timeout_s=10)
        return Response(content=twiml, media_type="text/xml")
    # Third strike: hang up cleanly.
    turn_log({
        "event": "hangup",
        "call_sid": call_sid,
        "reason": "timeout_max",
        "webhook_received_ms": t_recv,
    })
    _clear_timeout(call_sid)
    drop_session(call_sid)
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'{say_tag(HANGUP_TEXT)}<Hangup/></Response>'
    )
    return Response(content=twiml, media_type="text/xml")


@app.post("/reset")
async def reset() -> dict:
    """Extras: wipe session + timeout state for test resets without restart."""
    with sessions_lock:
        n = len(sessions)
        sessions.clear()
    with _timeout_counts_lock:
        _timeout_counts.clear()
    return {"status": "reset", "cleared_sessions": n}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8400, log_level="warning")
