"""EmpathyDirectiveComposer — lexical, turn-scoped empathy layer for the
scripted-response golden path (Path-A Phase 6d).

Ported from evaluation/founder-validation/empathy_directive.py. Trial-018
showed Kavya evaluated as robotic and lacking empathy even with V2 Ch14's
EmpathyPlanner + AdaptiveProsodyEngine running, because the deterministic
script templates in founder-validation bypassed that empathy layer — they
emitted static Hindi text with a default voice config, so the customer
never heard the acknowledgment/prosody dip. This module closes that gap for
the scripted golden path (Phase 6f's response engine):

  1. Classify the current customer utterance into a fine-grained hardship
     taxonomy (illness, job loss, salary delay, family, blunt financial
     hardship, resignation, frustration, anger, gratitude, relief, neutral).
  2. Produce an EmpathyDirective the response engine merges into the reply:
     an acknowledgment phrase spoken before the scripted ask, a listening
     pause, and rate/energy prosody deltas.

Fully deterministic — no LLM call. This is additive to, not a replacement
for, src/engines/empathy/EmpathyPlanner: EmpathyPlanner drives the coarse
StressLevel/Sentiment-based EmpathyConfig consumed by the LLM-authored
fallback path; this composer extends the empathy contract onto the
scripted golden path with finer-grained lexical acknowledgments.

Architecture: V2 Ch14 (EmpathyPlanner); V1 Ch20 (AdaptiveProsodyEngine).
"""

from __future__ import annotations

import re

from src.engines.empathy_directive.directive import EmpathyDirective, EmpathyState

# ---------------------------------------------------------------------------
# Classifier — bilingual pattern matchers
# ---------------------------------------------------------------------------
# Patterns are ordered from most specific -> least specific inside each
# bucket. Roman patterns are matched case-insensitively (word-boundary
# aware) against a normalized copy of the utterance; Devanagari patterns are
# matched as exact substrings against the raw utterance.

_PATTERNS: tuple[tuple[EmpathyState, tuple[str, ...]], ...] = (
    (EmpathyState.HARDSHIP_ILLNESS, (
        "tabiyat kharab", "tabiyat kharaab", "bimari", "bimar", "hospital",
        "operation", "ilaaj", "ilaj", "medical",
        "तबियत", "बीमारी", "बीमार", "अस्पताल", "इलाज",
    )),  # fmt: skip
    (EmpathyState.HARDSHIP_JOB_LOSS, (
        "job chali gayi", "job chali gayee", "naukri chali gayi",
        "naukri chhut gayi", "job nahi", "kaam chhut", "layoff", "nikal diya",
        "नौकरी चली गई", "नौकरी छूट गई", "काम छूट गया",
    )),  # fmt: skip
    (EmpathyState.HARDSHIP_SALARY_DLY, (
        "salary nahi aayi", "salary abhi tak nahi", "salary delay",
        "salary late", "salary aane wali hai", "salary aayegi",
        "salary ke baad", "salary aane par", "pagar nahi aayi",
        "सैलरी नहीं", "पगार नहीं", "पगार आने पर", "सैलरी आने पर",
        "सैलरी के बाद",
    )),  # fmt: skip
    (EmpathyState.HARDSHIP_FAMILY, (
        "ghar mein problem", "ghar ki problem", "family problem",
        "papa bimar", "maa bimar", "bacche", "shaadi",
        "घर में", "घर की problem", "परिवार", "बच्चे",
    )),  # fmt: skip
    (EmpathyState.HARDSHIP_FINANCIAL, (
        "paise nahi hain", "paise nahi hai", "paisa nahi", "paisa nahin",
        "abhi paise", "kuchh nahi hai", "kuch bhi nahi", "tight hai",
        "budget tight", "haath tang", "haath khaali", "financial problem",
        "पैसे नहीं", "पैसा नहीं", "पैसा नहीं है", "हाथ तंग", "हाथ खाली",
    )),  # fmt: skip
    (EmpathyState.FRUSTRATION, (
        "arre yaar", "arey", "kitni baar", "har baar", "kya baat",
        "chhod do", "chodo", "छोड़ो", "छोड़ दो", "कितनी बार",
    )),  # fmt: skip
    (EmpathyState.ANXIETY, (
        "tension", "worried", "chinta", "pareshan", "chintit",
        "टेंशन", "चिंता", "परेशान",
    )),  # fmt: skip
    (EmpathyState.RESIGNATION, (
        "kya karun", "kya karoon", "majboor", "majboori", "koi option",
        "kuchh nahi kar sakta", "क्या करूं", "मजबूर", "मजबूरी",
    )),  # fmt: skip
    (EmpathyState.ANGER, (
        "chup", "band karo", "phone rakh", "harass",
        "चुप", "बंद करो", "फोन रखो",
    )),  # fmt: skip
    (EmpathyState.GRATITUDE, (
        "thank you", "thanks", "shukriya", "dhanyavaad",
        "शुक्रिया", "धन्यवाद",
    )),  # fmt: skip
    (EmpathyState.RELIEF, (
        # Also plain acks; hardship/anger buckets are checked first above so
        # these only match when no stronger hardship signal is present.
        "achha theek", "theek hai bhai", "hmm okay",
        "अच्छा ठीक", "ठीक है भाई",
    )),  # fmt: skip
)


class EmpathyStateClassifier:
    """Rule-based classifier — bilingual, order-sensitive, deterministic."""

    def __init__(self) -> None:
        self._matchers: list[tuple[EmpathyState, tuple[re.Pattern[str] | str, ...]]] = []
        for state, tokens in _PATTERNS:
            compiled: list[re.Pattern[str] | str] = []
            for tok in tokens:
                if any("ऀ" <= ch <= "ॿ" for ch in tok):
                    compiled.append(tok)
                else:
                    compiled.append(re.compile(r"(?<!\w)" + re.escape(tok) + r"(?!\w)", flags=re.IGNORECASE))
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
                elif tok.search(lower):
                    return state
        return EmpathyState.NEUTRAL


# ---------------------------------------------------------------------------
# Directive composer
# ---------------------------------------------------------------------------

_ACK: dict[EmpathyState, str] = {
    EmpathyState.NEUTRAL: "",
    EmpathyState.HARDSHIP_FINANCIAL: "अच्छा sir, समझ रही हूँ। ",
    EmpathyState.HARDSHIP_ILLNESS: "अरे sir, tabiyat का सुनकर बुरा लगा। ",
    EmpathyState.HARDSHIP_JOB_LOSS: "समझ सकती हूँ sir, ये situation आसान नहीं है। ",
    EmpathyState.HARDSHIP_SALARY_DLY: "अच्छा, salary का issue है — बात समझ आ रही है। ",
    EmpathyState.HARDSHIP_FAMILY: "समझ रही हूँ sir, ghar ki priority पहले है। ",
    EmpathyState.FRUSTRATION: "समझ रही हूँ sir, बात करते हैं। ",
    EmpathyState.ANXIETY: "chinta मत कीजिए sir, साथ हैं आपके। ",
    EmpathyState.RESIGNATION: "साथ मिलकर रास्ता निकालते हैं sir। ",
    EmpathyState.ANGER: "माफ़ कीजिए sir, एक moment। ",
    EmpathyState.GRATITUDE: "",
    EmpathyState.RELIEF: "",
}

_PROSODY: dict[EmpathyState, tuple[float, float, int]] = {
    # state:                          (rate_delta, energy_delta, break_ms)
    EmpathyState.NEUTRAL: (0.00, 0.00, 0),
    EmpathyState.HARDSHIP_FINANCIAL: (-0.08, -0.10, 350),
    EmpathyState.HARDSHIP_ILLNESS: (-0.10, -0.12, 500),
    EmpathyState.HARDSHIP_JOB_LOSS: (-0.08, -0.10, 400),
    EmpathyState.HARDSHIP_SALARY_DLY: (-0.06, -0.08, 300),
    EmpathyState.HARDSHIP_FAMILY: (-0.08, -0.10, 400),
    EmpathyState.FRUSTRATION: (-0.05, -0.05, 250),
    EmpathyState.ANXIETY: (-0.07, -0.08, 350),
    EmpathyState.RESIGNATION: (-0.06, -0.08, 300),
    EmpathyState.ANGER: (-0.05, -0.05, 400),
    EmpathyState.GRATITUDE: (0.00, 0.00, 0),
    EmpathyState.RELIEF: (0.00, 0.00, 0),
}

_FAREWELL_BUCKETS = frozenset({"farewell", "loan_denial"})


class EmpathyDirectiveComposer:
    """Deterministic composer: user_text -> EmpathyDirective."""

    def __init__(self) -> None:
        self._classifier = EmpathyStateClassifier()

    def compose(self, user_text: str, bucket: str) -> EmpathyDirective:
        state = self._classifier.classify(user_text)
        if bucket in _FAREWELL_BUCKETS:
            # Don't lecture the customer with an empathy prefix on a farewell.
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

        The acknowledgment is terminated with a Devanagari "।" so downstream
        SSML preparation inserts a natural pause before the reply continues,
        on top of the prosody dip (rate/energy) the caller applies to the
        turn's voice config from this directive.
        """
        if directive.state == EmpathyState.NEUTRAL or not directive.acknowledgment:
            return base_reply
        ack = directive.acknowledgment
        if not ack.rstrip().endswith(("।", ".", "!", "?")):
            ack = ack.rstrip() + "। "
        elif not ack.endswith(" "):
            ack = ack + " "
        return ack + base_reply


__all__ = ["EmpathyDirectiveComposer", "EmpathyStateClassifier"]
