"""EmpathyDirectiveComposer — permanent empathy layer for the Kavya voice-bot.

Purpose
-------
Volume 2 (Ch. 14) defines an EmpathyPlanner + AdaptiveProsodyEngine pair whose
output already flows into VoiceConfig. Trial-018 showed that even with those
engines running, Omni evaluated Kavya as *robotic and lacking empathy*. Root
cause: the deterministic script templates in the founder-validation pipeline
bypass the empathy layer — they emit static Hindi text with a default
VoiceConfig, so the customer never hears the acknowledgment/prosody dip that
Volume 2 promises.

This module closes that gap. It is a **permanent engine**, not a sprinkled
fix. It has two responsibilities:

  1. Classify the current customer utterance into a fine-grained emotional
     state drawn from a hardship taxonomy that Kajal actually encounters in
     Indian collections calls (illness, job loss, salary delay, family, blunt
     financial hardship, resignation, frustration, anger, gratitude, relief,
     neutral).

  2. Produce an ``EmpathyDirective`` that the state machine merges into the
     scripted reply:
       * ``acknowledgment`` — a short bilingual token spoken *before* the
         scripted ask (e.g. "अच्छा… समझ रही हूँ sir।").
       * ``listening_break_ms`` — SSML ``<break>`` prepended so Kajal sounds
         like she paused to absorb.
       * ``rate_scale_delta``, ``energy_scale_delta`` — modifiers applied to
         the outgoing VoiceConfig so the prosody softens on empathy states.
       * ``allow_close_on_ack`` — routing hint used by the bucket router.

The composer is fully deterministic. It does not call the LLM. It coexists
with the existing EmpathyPlanner (which drives the LLM-authored replies) —
this module extends the empathy contract onto the scripted golden path.

Architecture: V2 Ch14 (EmpathyPlanner) · V1 Ch20 (AdaptiveProsodyEngine).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# Empathy taxonomy
# ---------------------------------------------------------------------------
class EmpathyState(str, Enum):
    """Fine-grained emotional states the composer distinguishes."""

    NEUTRAL              = "NEUTRAL"
    HARDSHIP_FINANCIAL   = "HARDSHIP_FINANCIAL"    # "abhi paise nahi hain"
    HARDSHIP_ILLNESS     = "HARDSHIP_ILLNESS"      # "ghar mein tabiyat kharaab"
    HARDSHIP_JOB_LOSS    = "HARDSHIP_JOB_LOSS"     # "job chali gayi"
    HARDSHIP_SALARY_DLY  = "HARDSHIP_SALARY_DLY"   # "salary abhi tak nahi aayi"
    HARDSHIP_FAMILY      = "HARDSHIP_FAMILY"       # "ghar ki problem"
    FRUSTRATION          = "FRUSTRATION"           # "arre yaar", "kitni baar"
    ANXIETY              = "ANXIETY"               # "tension mein hoon"
    RESIGNATION          = "RESIGNATION"           # "kya karun", "majboor hoon"
    ANGER                = "ANGER"                 # abuse markers
    GRATITUDE            = "GRATITUDE"             # "thank you", "shukriya"
    RELIEF               = "RELIEF"                # "achha", after agreement


_HARDSHIP_STATES: frozenset[EmpathyState] = frozenset({
    EmpathyState.HARDSHIP_FINANCIAL,
    EmpathyState.HARDSHIP_ILLNESS,
    EmpathyState.HARDSHIP_JOB_LOSS,
    EmpathyState.HARDSHIP_SALARY_DLY,
    EmpathyState.HARDSHIP_FAMILY,
})


# ---------------------------------------------------------------------------
# Directive
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EmpathyDirective:
    """Merge instructions produced for a single turn.

    Fields
    ------
    state : EmpathyState
        The classifier's verdict. Kept on the directive for logging/QA.
    acknowledgment : str
        Bilingual short phrase spoken before the scripted ask. Empty string
        when the state is NEUTRAL (no prefix injected).
    listening_break_ms : int
        Silence padded before Kajal opens her mouth. ~0 for NEUTRAL, 350-500
        on hardship states so it feels like she took a breath.
    rate_scale_delta : float
        Additive to VoiceConfig.rate_scale (softer/slower on hardship).
        Applied after clamp in the caller.
    energy_scale_delta : float
        Additive to VoiceConfig.energy_scale (quieter on hardship).
    allow_close_on_ack : bool
        Routing hint: when the customer sends a bare ack after a
        confirm_date/plan, the bucket router should hop to close rather than
        anchor again.
    """

    state: EmpathyState = EmpathyState.NEUTRAL
    acknowledgment: str = ""
    listening_break_ms: int = 0
    rate_scale_delta: float = 0.0
    energy_scale_delta: float = 0.0
    allow_close_on_ack: bool = True


# ---------------------------------------------------------------------------
# Classifier — bilingual pattern matchers
# ---------------------------------------------------------------------------
# Patterns are ordered from most specific → least specific inside each bucket.
# Roman patterns are matched case-insensitively against a normalized copy of
# the utterance; Devanagari patterns are matched against the raw utterance.

_PATTERNS: tuple[tuple[EmpathyState, tuple[str, ...]], tuple[EmpathyState, tuple[str, ...]], ...] = (  # type: ignore[assignment]
    (EmpathyState.HARDSHIP_ILLNESS, (
        "tabiyat kharab", "tabiyat kharaab", "bimari", "bimar", "hospital",
        "operation", "ilaaj", "ilaj", "medical",
        "तबियत", "बीमारी", "बीमार", "अस्पताल", "इलाज",
    )),
    (EmpathyState.HARDSHIP_JOB_LOSS, (
        "job chali gayi", "job chali gayee", "naukri chali gayi",
        "naukri chhut gayi", "job nahi", "kaam chhut", "layoff", "nikal diya",
        "नौकरी चली गई", "नौकरी छूट गई", "काम छूट गया",
    )),
    (EmpathyState.HARDSHIP_SALARY_DLY, (
        "salary nahi aayi", "salary abhi tak nahi", "salary delay",
        "salary late", "salary aane wali hai", "salary aayegi",
        "salary ke baad", "salary aane par", "pagar nahi aayi",
        "सैलरी नहीं", "पगार नहीं", "पगार आने पर", "सैलरी आने पर",
        "सैलरी के बाद",
    )),
    (EmpathyState.HARDSHIP_FAMILY, (
        "ghar mein problem", "ghar ki problem", "family problem",
        "papa bimar", "maa bimar", "bacche", "shaadi",
        "घर में", "घर की problem", "परिवार", "बच्चे",
    )),
    (EmpathyState.HARDSHIP_FINANCIAL, (
        "paise nahi hain", "paise nahi hai", "paisa nahi", "paisa nahin",
        "abhi paise", "kuchh nahi hai", "kuch bhi nahi", "tight hai",
        "budget tight", "haath tang", "haath khaali", "financial problem",
        "पैसे नहीं", "पैसा नहीं", "पैसा नहीं है", "हाथ तंग", "हाथ खाली",
    )),
    (EmpathyState.FRUSTRATION, (
        "arre yaar", "arey", "kitni baar", "har baar", "kya baat",
        "chhod do", "chodo", "छोड़ो", "छोड़ दो", "कितनी बार",
    )),
    (EmpathyState.ANXIETY, (
        "tension", "worried", "chinta", "pareshan", "chintit",
        "टेंशन", "चिंता", "परेशान",
    )),
    (EmpathyState.RESIGNATION, (
        "kya karun", "kya karoon", "majboor", "majboori", "koi option",
        "kuchh nahi kar sakta", "क्या करूं", "मजबूर", "मजबूरी",
    )),
    (EmpathyState.ANGER, (
        "chup", "band karo", "phone rakh", "harass",
        "चुप", "बंद करो", "फोन रखो",
    )),
    (EmpathyState.GRATITUDE, (
        "thank you", "thanks", "shukriya", "dhanyavaad",
        "शुक्रिया", "धन्यवाद",
    )),
    (EmpathyState.RELIEF, (
        # These are also plain acks; classifier requires them to be ONLY signal
        # (no hardship/anger co-signal) via ordering — hardship is checked first.
        "achha theek", "theek hai bhai", "hmm okay",
        "अच्छा ठीक", "ठीक है भाई",
    )),
)


class EmpathyStateClassifier:
    """Rule-based classifier — bilingual, order-sensitive, deterministic."""

    def __init__(self) -> None:
        # Precompile regexes for the Roman patterns (word-boundary aware).
        self._matchers: list[tuple[EmpathyState, tuple[re.Pattern[str] | str, ...]]] = []
        for state, tokens in _PATTERNS:
            compiled: list[re.Pattern[str] | str] = []
            for tok in tokens:
                if any("\u0900" <= ch <= "\u097f" for ch in tok):
                    compiled.append(tok)                       # exact Devanagari match
                else:
                    compiled.append(re.compile(
                        r"(?<!\w)" + re.escape(tok) + r"(?!\w)",
                        flags=re.IGNORECASE,
                    ))
            self._matchers.append((state, tuple(compiled)))

    def classify(self, user_text: str) -> EmpathyState:
        if not user_text:
            return EmpathyState.NEUTRAL
        lower = user_text.lower()
        for state, tokens in self._matchers:
            for tok in tokens:
                if isinstance(tok, str):
                    if tok in user_text:
                        return state
                else:
                    if tok.search(lower):
                        return state
        return EmpathyState.NEUTRAL


# ---------------------------------------------------------------------------
# Directive composer
# ---------------------------------------------------------------------------
# Acknowledgment library — one bilingual phrase per state. The phrases are
# short (~2 s speech) so they don't dilate the turn budget.
_ACK: dict[EmpathyState, str] = {
    EmpathyState.NEUTRAL:             "",
    EmpathyState.HARDSHIP_FINANCIAL:  "अच्छा sir, समझ रही हूँ। ",
    EmpathyState.HARDSHIP_ILLNESS:    "अरे sir, tabiyat का सुनकर बुरा लगा। ",
    EmpathyState.HARDSHIP_JOB_LOSS:   "समझ सकती हूँ sir, ये situation आसान नहीं है। ",
    EmpathyState.HARDSHIP_SALARY_DLY: "अच्छा, salary का issue है — बात समझ आ रही है। ",
    EmpathyState.HARDSHIP_FAMILY:     "समझ रही हूँ sir, ghar ki priority पहले है। ",
    EmpathyState.FRUSTRATION:         "समझ रही हूँ sir, बात करते हैं। ",
    EmpathyState.ANXIETY:             "chinta मत कीजिए sir, साथ हैं आपके। ",
    EmpathyState.RESIGNATION:         "साथ मिलकर रास्ता निकालते हैं sir। ",
    EmpathyState.ANGER:               "माफ़ कीजिए sir, एक moment। ",
    EmpathyState.GRATITUDE:           "",
    EmpathyState.RELIEF:              "",
}


# Per-state prosody dips. Values are additive deltas on VoiceConfig scales.
# Rate: negative → slower; Energy: negative → quieter.
_PROSODY: dict[EmpathyState, tuple[float, float, int]] = {
    # state:                          (rate_delta, energy_delta, break_ms)
    EmpathyState.NEUTRAL:             ( 0.00,  0.00,   0),
    EmpathyState.HARDSHIP_FINANCIAL:  (-0.08, -0.10, 350),
    EmpathyState.HARDSHIP_ILLNESS:    (-0.10, -0.12, 500),
    EmpathyState.HARDSHIP_JOB_LOSS:   (-0.08, -0.10, 400),
    EmpathyState.HARDSHIP_SALARY_DLY: (-0.06, -0.08, 300),
    EmpathyState.HARDSHIP_FAMILY:     (-0.08, -0.10, 400),
    EmpathyState.FRUSTRATION:         (-0.05, -0.05, 250),
    EmpathyState.ANXIETY:             (-0.07, -0.08, 350),
    EmpathyState.RESIGNATION:         (-0.06, -0.08, 300),
    EmpathyState.ANGER:               (-0.05, -0.05, 400),
    EmpathyState.GRATITUDE:           ( 0.00,  0.00,   0),
    EmpathyState.RELIEF:              ( 0.00,  0.00,   0),
}


class EmpathyDirectiveComposer:
    """Deterministic composer: user_text → EmpathyDirective.

    Coexists with (and is designed to be fed with) the Volume 2 EmpathyPlanner
    and AdaptiveProsodyEngine: the planner captures broad tone/pacing signals
    from the emotion engine, while this composer produces the *lexical* and
    *turn-scoped* directive that gets merged into the scripted reply.
    """

    def __init__(self) -> None:
        self._classifier = EmpathyStateClassifier()

    def compose(self, user_text: str, bucket: str) -> EmpathyDirective:
        state = self._classifier.classify(user_text)
        # Bucket-specific overrides — don't lecture the customer on a farewell.
        if bucket in ("farewell", "loan_denial"):
            state = EmpathyState.NEUTRAL
        ack = _ACK[state]
        rate_d, energy_d, brk = _PROSODY[state]
        return EmpathyDirective(
            state=state,
            acknowledgment=ack,
            listening_break_ms=brk,
            rate_scale_delta=rate_d,
            energy_scale_delta=energy_d,
            allow_close_on_ack=True,
        )

    def apply_to_reply(self, base_reply: str, directive: EmpathyDirective) -> str:
        """Prepend the bilingual acknowledgment to a scripted reply.

        The acknowledgment ends with "।<space>" so the pipeline's Devanagari
        pause marker (``_BREAK_150``) inserts a natural inhale before Kajal
        continues with the ask. We deliberately do NOT emit a raw
        ``<break time="…"/>`` tag here — that would be XML-escaped by the
        SSML prepare stage. The perceived pause comes from:

          1. the "।" sentence terminator that ``_finalize_markers`` maps to a
             150 ms break;
          2. the prosody dip (rate/energy) applied by the caller to the
             VoiceConfig for this whole reply;
          3. the acknowledgment tokens themselves being softly cadenced.
        """
        if directive.state == EmpathyState.NEUTRAL:
            return base_reply
        if not directive.acknowledgment:
            return base_reply
        # Ensure a Devanagari terminator so prepare_ssml inserts the break.
        ack = directive.acknowledgment
        if not ack.rstrip().endswith(("।", ".", "!", "?")):
            ack = ack.rstrip() + "। "
        elif not ack.endswith(" "):
            ack = ack + " "
        return ack + base_reply


__all__ = [
    "EmpathyState",
    "EmpathyDirective",
    "EmpathyStateClassifier",
    "EmpathyDirectiveComposer",
]
