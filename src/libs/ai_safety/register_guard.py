"""RegisterGuard — reject-and-replace guard for LLM register/tone/hallucination
defects in collections-call replies (Path-A Phase 6).

Ported from evaluation/founder-validation/conv_server.py's register-guard
system (Classes 1/3 in that script's own numbering) — a defense-in-depth
layer distinct from AIGovernanceService/LawOfAuthorityChecker: the Law of
Authority checks *factual* claims against ResponsePlan's provenance-tagged
facts; this guard checks *register* (literary/Sanskritized words that sound
unnatural on a phone call), *grammar* (masculine verb forms from a female
persona), *tone* (slang/harassment), *scope* (offering unsolicited follow-ups,
claiming physical actions the agent can't perform), and *hallucinated
disclaimers* (call-recording announcements, discounts/waivers nobody
authorized) — none of which LawOfAuthorityChecker's fact-provenance model
covers, and none of which existed anywhere in src/ before this port.

Unlike conv_server.py's version, name-scrubbing here is NOT hardcoded to one
customer ("Prateek Das") — it's parameterized by the actual customer name
from CustomerContext, since Path A serves every tenant's customers, not one
founder demo account.

Architecture: V4 Ch14 (AI Safety); V6 AR-7 (no hardcoded policy in prompts —
this guard is the code-side backstop when the LLM ignores the prompt rules).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

# ---------------------------------------------------------------------------
# Register categories (ported verbatim from conv_server.py's blocklists)
# ---------------------------------------------------------------------------


class RegisterViolation(StrEnum):
    """Why a reply was rejected — mirrors conv_server.py's guard_action tags."""

    FOREIGN_SCRIPT = "foreign_script"
    LITERARY = "literary"
    SLANG = "slang"
    MASCULINE_GRAMMAR = "masc_me"
    ACTION_HALLUCINATION = "action_hallucination"
    UNSOLICITED_FOLLOWUP = "unsolicited_followup"
    HALLUCINATION = "hallucination"


_LITERARY = (
    "भुगतान", "कृपया", "प्रतीत", "अवगत", "राशि", "वाक्य", "अंतिम",
    "अवशेष", "रात्रि", "धन्यवाद", "समक्ष", "विवरण",
)  # fmt: skip
"""Genuinely Sanskritized words that sound literary, not Delhi-office-casual, on a call."""

_SLANG = ("साला", "साली", "अबे", "भोसड़", "चूतिय", "कमीन", "यार ")  # trailing space intentional
"""Rude/slang words — never appropriate regardless of customer behavior."""

_MASCULINE_GRAMMAR = (
    "कर दूंगा", "दे दूंगा", "करूँगा", "करूंगा", "दूँगा", "दूंगा",
    "बताऊँगा", "बताऊंगा", "लूँगा", "लूंगा", "सकूँगा", "सकूंगा",
    "आया हूँ", "आया हूं", "गया हूँ", "गया हूं", "हुआ हूँ", "हुआ हूं",
    "बैठा हूँ", "बैठा हूं", "आया था", "गया था", "बोला था",
    "कर सकता", "बोल रहा हूँ", "बोल रहा हूं", "जा रहा हूँ", "जा रहा हूं",
    "समझ सकता",
    "कर देगे", "कर देगा", "कर देंगा", "कर देगें",
    "कर देगे?", "कर देगा?", "कर देगे।",
    "पे payment कर देगे", "payment कर देगे",
)  # fmt: skip
"""Masculine first-person verb forms — the persona is female; these must never appear."""

_ACTION_HALLUCINATIONS = (
    "मैं बैंक", "बैंक जा", "system में देख", "अभी जाकर", "मैं system",
    "अपने laptop", "अपनी system",
)  # fmt: skip
"""Claims of physical actions a phone agent cannot perform (visit bank, check system)."""

_UNSOLICITED_FOLLOWUPS = ("हर महीने कॉल", "हर महीने call", "monthly call", "monthly कॉल", "हर महीने बात", "रोज़ कॉल")  # fmt: skip
"""Offers of recurring contact the customer never asked for."""

_HALLUCINATIONS = (
    "call recording", "recording ki ja", "रिकॉर्ड की जा", "रिकॉर्डिंग की जा",
    "record ki ja", "recording ho rahi", "रिकॉर्डिंग हो रही",
    "call record", "call ki recording",
    "discount दे सकते", "waiver दे सकते", "interest माफ",
)  # fmt: skip
"""Recording-consent announcements (the platform's job, never the agent's) and
unauthorized policy inventions (discounts/waivers/interest waivers)."""

_FOREIGN_SCRIPT_RE = re.compile(r"[一-鿿぀-ヿ؀-ۿ]")
"""CJK/Arabic code-switching — only Devanagari + Latin are ever legitimate."""

_CATEGORY_ORDER: tuple[tuple[RegisterViolation, tuple[str, ...]], ...] = (
    (RegisterViolation.LITERARY, _LITERARY),
    (RegisterViolation.SLANG, _SLANG),
    (RegisterViolation.MASCULINE_GRAMMAR, _MASCULINE_GRAMMAR),
    (RegisterViolation.ACTION_HALLUCINATION, _ACTION_HALLUCINATIONS),
    (RegisterViolation.UNSOLICITED_FOLLOWUP, _UNSOLICITED_FOLLOWUPS),
    (RegisterViolation.HALLUCINATION, _HALLUCINATIONS),
)


@dataclass(frozen=True)
class RegisterCheckResult:
    """Outcome of RegisterGuard.check()."""

    clean: bool
    violation: RegisterViolation | None = None


class RegisterGuard:
    """Deterministic reject-and-replace checker for reply register/tone/hallucination defects."""

    def check(self, reply: str) -> RegisterCheckResult:
        """Return the first matching violation category, or a clean result."""
        if _FOREIGN_SCRIPT_RE.search(reply):
            return RegisterCheckResult(clean=False, violation=RegisterViolation.FOREIGN_SCRIPT)
        lower = reply.lower()
        for category, tokens in _CATEGORY_ORDER:
            for token in tokens:
                if token in reply or token.lower() in lower:
                    return RegisterCheckResult(clean=False, violation=category)
        return RegisterCheckResult(clean=True)


# ---------------------------------------------------------------------------
# Name scrub — parameterized by the actual customer name (never hardcoded)
# ---------------------------------------------------------------------------


def _build_name_patterns(customer_name: str) -> list[re.Pattern[str]]:
    """Build Roman-script name-removal patterns from the customer's CRM name.

    Known limitation (unlike conv_server.py's hardcoded "प्रतीक" patterns for
    its one demo customer): this only covers the Roman-script spelling as
    recorded in CustomerContext — it does not auto-generate Devanagari
    transliteration variants, since that requires either a transliteration
    library (new dependency, not yet justified) or a CRM-side Devanagari
    name field (not present in PartyInfo today). Flagged here rather than
    silently hardcoding one customer's Devanagari spelling for everyone.
    """
    name = customer_name.strip()
    if not name:
        return []
    parts = [p for p in re.split(r"\s+", name) if p]
    patterns: list[re.Pattern[str]] = []
    if len(parts) >= 2:
        full = r"\s+".join(re.escape(p) for p in parts)
        patterns.append(re.compile(rf",?\s*{full}\s+sir\s*", re.IGNORECASE))
        patterns.append(re.compile(rf",?\s*{full}\s*", re.IGNORECASE))
    first = re.escape(parts[0])
    patterns.append(re.compile(rf",?\s*{first}\s+sir\s*", re.IGNORECASE))
    patterns.append(re.compile(rf",?\s*{first}\s*", re.IGNORECASE))
    return patterns


def dedupe_name(reply: str, customer_name: str) -> str:
    """Strip direct address-by-name — the persona addresses customers as
    'sir', never by name (rule 6 of the Kavya persona, see
    src/engines/prompt_builder/kavya_persona.py)."""
    if not customer_name:
        return reply
    changed = reply
    for pattern in _build_name_patterns(customer_name):
        changed = pattern.sub(" ", changed)
    changed = re.sub(r"\s+", " ", changed).strip()
    changed = re.sub(r"\s+([।,.?!])", r"\1", changed)
    changed = re.sub(r"^([,.।])\s*", "", changed)
    return changed or reply


# ---------------------------------------------------------------------------
# Final sanitize pass — residual fragment purge
# ---------------------------------------------------------------------------

_SANITIZE_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"कोई\s+नहीं(?=\s*[।,.?!]|\s*$)"), "कोई बात नहीं"),
    (re.compile(r"(?<![क-हa-zA-Z])आई हूँ(?![क-हa-zA-Z])"), "बात कर रही हूँ"),
    (re.compile(r"(?<![क-हa-zA-Z])आई हूं(?![क-हa-zA-Z])"), "बात कर रही हूँ"),
)


def sanitize_reply(reply: str) -> str:
    """Final purge of residual fragments after the LLM + other guards run."""
    out = reply
    for pattern, replacement in _SANITIZE_REPLACEMENTS:
        out = pattern.sub(replacement, out)
    return out


# ---------------------------------------------------------------------------
# Trailing "sir" strip + cap
# ---------------------------------------------------------------------------

_SIR_TAIL_PATTERNS = (
    re.compile(r",\s*sir\s*[।.!?]$", re.IGNORECASE),
    re.compile(r"\s+sir\s*[।.!?]$", re.IGNORECASE),
    re.compile(r",\s*sir\s*$", re.IGNORECASE),
    re.compile(r"\s+sir\s*$", re.IGNORECASE),
)


def strip_trailing_sir(reply: str) -> str:
    """Strip a trailing 'sir' filler and cap 'sir' mentions at one per reply
    (persona rule 5: 'sir' only inline at the start of a sentence, never as
    tail filler, at most once every few turns)."""
    changed = reply
    for pattern in _SIR_TAIL_PATTERNS:
        new = pattern.sub("।" if changed.rstrip().endswith(("।", ".", "!", "?")) else "", changed)
        if new != changed:
            changed = new
            break
    parts = re.split(r"(?i)(\bsir\b)", changed)
    seen = 0
    out: list[str] = []
    for part in parts:
        if part.lower() == "sir":
            seen += 1
            if seen > 1:
                continue
        out.append(part)
    changed = "".join(out)
    changed = re.sub(r"\s+", " ", changed).strip()
    changed = re.sub(r"\s+([।,.?!])", r"\1", changed)
    return changed or reply
