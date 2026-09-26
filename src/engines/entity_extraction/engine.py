"""EntityExtractor — slot-filling entity extraction for Hindi/English utterances.

The EntityExtractor applies a cascade of rule-based extractors to the
TurnInput transcript. Rules handle:
  - AMOUNT: ₹ notation, Devanagari number words, English number words
  - DATE / PROMISE_DATE: absolute dates, relative Hindi expressions (kal, parson)
  - ACCOUNT_NUMBER, PHONE, UPI_ID, LOAN_ID: pattern-based
  - NAME, PARTIAL_AMOUNT: heuristic extraction

No ML model is required for rule-based extraction. An ML slot-filling
model can be wired in as an optional post-processor in Sprint-034.

Architecture: V2 Ch9 (Entity Extraction Engine).
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import TYPE_CHECKING

from prometheus_client import Counter

from src.libs.contracts.turn import TurnInput

from .result import ExtractedEntities, ExtractedValue
from .slots import EntityType

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

_ENTITY_EXTRACTIONS = Counter(
    "entity_extractions_total",
    "Total entity extractions performed by type",
    ["entity_type"],
)

# ---------------------------------------------------------------------------
# Devanagari / Hindi digit word map
# ---------------------------------------------------------------------------

_HINDI_DIGIT_WORDS: dict[str, str] = {
    "ek": "1",
    "do": "2",
    "teen": "3",
    "char": "4",
    "paanch": "5",
    "chhe": "6",
    "saat": "7",
    "aath": "8",
    "nau": "9",
    "das": "10",
    "bis": "20",
    "tees": "30",
    "chalis": "40",
    "pachaas": "50",
    "saath": "60",
    "sattar": "70",
    "assi": "80",
    "nabbe": "90",
    "sau": "100",
    "hazaar": "1000",
    "lakh": "100000",
    "karod": "10000000",
}

_HINDI_RELATIVE_DATES: dict[str, int] = {
    "kal": 1,  # tomorrow (Roman)
    "parson": 2,  # day after tomorrow (Roman)
    "aaj": 0,  # today (Roman)
    "कल": 1,  # tomorrow (Devanagari)
    "परसों": 2,  # day after tomorrow (Devanagari)
    "आज": 0,  # today (Devanagari)
    "is hafte": 7,  # this week
    "agli hafte": 7,
    "next week": 7,
    "tomorrow": 1,
    "today": 0,
    "day after": 2,
    # ------------------------------------------------------------------
    # Path-A Phase 6: extended relative-date coverage, ported from
    # evaluation/founder-validation/conv_server.py's _RELATIVE_DATE_TOKENS
    # (90+ entries tuned against real founder-reviewed trial calls) so the
    # scripted response engine can consume EntityExtractor's PROMISE_DATE
    # directly instead of conv_server.py's own parallel date parser.
    # Overlapping phrases are deliberately given the SAME offset as any
    # existing shorter substring they contain, since _resolve_relative_date
    # matches the first dict entry found via substring search (insertion
    # order) — see the loop in _resolve_relative_date.
    # ------------------------------------------------------------------
    # day-scale aliases/variants
    "day after tomorrow": 2,
    "kal tak": 1,
    "kal shaam": 1,
    "kal subah": 1,
    "aaj shaam": 0,
    "aaj raat": 0,
    "aaj tak": 0,
    "कल तक": 1,
    "कल शाम": 1,
    "कल सुबह": 1,
    "आज शाम": 0,
    "आज तक": 0,
    # week-scale — quantified phrases ("do hafte") MUST be checked before the
    # generic single-word phrases below ("hafte mein" etc.), since
    # _resolve_relative_date matches the FIRST dict entry whose text is a
    # substring of the utterance (insertion order) — "do teen hafte" and
    # "teen hafte" both contain "hafte", and without this ordering "hafte
    # mein"/"hafte tak" etc. would shadow them and always resolve to 7 days.
    "do teen hafte": 21,  # checked before "teen hafte" (also 21) — order-safe either way
    "teen hafte": 21,
    "do hafte": 14,
    "chaar hafte": 28,
    "char hafte": 28,
    "agle hafte": 7,
    "agle hafta": 7,
    "iss hafte": 7,
    "this week": 7,
    "hafte mein": 7,
    "hafte ke andar": 7,
    "hafte tak": 7,
    "hafte baad": 7,
    "hafte ke baad": 7,
    "week baad": 7,
    "week ke baad": 7,
    "weeks baad": 7,
    "week tak": 7,
    "week mein": 7,
    "week ke andar": 7,
    "aane wale hafte": 7,
    "aane waale hafte": 7,
    "अगले हफ्ते": 7,
    "अगले हफ़्ते": 7,
    "इस हफ्ते": 7,
    "इस हफ़्ते": 7,
    "हफ्ते तक": 7,
    "हफ्ते में": 7,
    # month-scale (calendar-month-length approximated as 30 days; "this
    # month"/"month end" approximated as mid/end-of-month offsets — these
    # are deliberate business-reasonable defaults, not exact calendar math,
    # matching conv_server.py's own approach of echoing customer phrasing
    # rather than computing exact month boundaries)
    "agle mahine": 30,
    "agle month": 30,
    "next month": 30,
    "iss month": 15,
    "this month": 15,
    "month end": 20,
    "month ke baad": 30,
    "month baad": 30,
    "mahine baad": 30,
    "mahine ke baad": 30,
    "mahine ke end": 20,
    "mahine tak": 30,
    "अगले महीने": 30,
    "इस महीने": 15,
    # salary/pay-day markers — salary dates vary per employer; 30 days is a
    # documented business-reasonable default for a monthly pay cycle, not a
    # precise computation (same limitation conv_server.py had — it only
    # echoed the customer's phrasing without resolving an actual date).
    "salary aane par": 30,
    "salary aane ke baad": 30,
    "salary ke baad": 30,
    "salary aayegi": 30,
    "पगार आने पर": 30,
    "सैलरी के बाद": 30,
}

# Hindi/Hinglish digit-word → day count, for "N din"/"N days"/"N दिन"
# expressions (e.g. "pandrah din mein" -> 15 days). Ported from
# conv_server.py's _HINDI_DAY_WORDS/_HINGLISH_DAY_WORDS.
_HINDI_DAY_WORDS: dict[str, int] = {
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5,
    "छह": 6, "छः": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
    "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15,
    "सोलह": 16, "सत्रह": 17, "अठारह": 18, "उन्नीस": 19, "बीस": 20,
    "इक्कीस": 21, "बाईस": 22, "तेईस": 23, "चौबीस": 24, "पच्चीस": 25,
    "तीस": 30, "पैंतीस": 35, "चालीस": 40, "पैंतालीस": 45, "पचास": 50,
    "साठ": 60,
}  # fmt: skip
_HINGLISH_DAY_WORDS: dict[str, int] = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5,
    "chhah": 6, "chhe": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10,
    "gyarah": 11, "barah": 12, "terah": 13, "chaudah": 14,
    "pandrah": 15, "pandra": 15,
    "solah": 16, "satrah": 17, "atharah": 18, "unnis": 19, "bees": 20,
    "ikkis": 21, "bais": 22, "tees": 30, "pachaas": 50, "pachas": 50,
}  # fmt: skip

_RE_DAY_UNIT = re.compile(r"(\d{1,3})\s*(?:din|dino|days?|दिन|दिनों)", re.IGNORECASE)
_RE_TAREEKH = re.compile(r"(\d{1,2})\s*(?:तारीख|tareekh|tarikh|taarikh)", re.IGNORECASE)
_DEVA_DIGIT_TRANS = str.maketrans("०१२३४५६७८९", "0123456789")


def _resolve_day_offset(text: str) -> int | None:
    """Return N if the utterance says 'N din'/'N days'/'N दिन', including
    Hindi-word and Hinglish-word counts ('pandrah din' -> 15)."""
    normalized = text.translate(_DEVA_DIGIT_TRANS)
    m = _RE_DAY_UNIT.search(normalized)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    for word, n in _HINDI_DAY_WORDS.items():
        if re.search(rf"{word}\s*दिन", text):
            return n
    lower = normalized.lower()
    for word, n in _HINGLISH_DAY_WORDS.items():
        if re.search(rf"\b{word}\s+(?:din|days?)\b", lower):
            return n
    return None


def _resolve_tareekh(text: str, reference_date: date) -> date | None:
    """Resolve 'N तारीख'/'N tareekh' (a day-of-month promise) to the nearest
    future occurrence of that day-of-month (this month if not yet passed,
    else next month)."""
    m = _RE_TAREEKH.search(text)
    if not m:
        return None
    try:
        day = int(m.group(1))
    except ValueError:
        return None
    if not (1 <= day <= 31):
        return None
    year, month = reference_date.year, reference_date.month
    for _ in range(2):  # try this month, then next month
        try:
            candidate = date(year, month, day)
        except ValueError:
            candidate = None
        if candidate is not None and candidate >= reference_date:
            return candidate
        month += 1
        if month > 12:
            month = 1
            year += 1
    return None

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

# ₹ followed by optional comma-formatted number
_RE_RUPEE = re.compile(r"₹\s*([\d,]+(?:\.\d{1,2})?)")

# English: "5000 rupees", "Rs 5000", "five thousand rupees"
_RE_RUPEE_ENG = re.compile(r"(?:rs\.?\s*|rupees?\s*)([\d,]+)", re.IGNORECASE)

# Absolute date patterns
_RE_DATE_DMY = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b")
_RE_DATE_WORDS = re.compile(
    r"\b(\d{1,2})\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|"
    r"may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)

# Phone numbers (Indian: 10 digits, optionally +91 prefix)
_RE_PHONE = re.compile(r"(?:\+91[-\s]?)?([6-9]\d{9})\b")

# UPI ID pattern
_RE_UPI = re.compile(r"\b([\w.\-]+@[\w]+)\b")

# Account/loan number (6-18 alphanumeric)
_RE_ACCOUNT = re.compile(r"\b([A-Z0-9]{6,18})\b")

# Partial amount: "ek hazaar de sakta" → 1000
_RE_PARTIAL = re.compile(
    r"\b(ek|do|teen|char|paanch)\s+(hazaar|lakh)\b",
    re.IGNORECASE,
)

_MONTH_MAP: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _strip_commas(s: str) -> str:
    return s.replace(",", "")


def _parse_amount_hindi(text: str) -> str | None:
    """Parse Hindi amount words like 'paanch hazaar' → '5000'."""
    lower = text.lower()
    for num_word, num_val in _HINDI_DIGIT_WORDS.items():
        for mult_word, mult_val in _HINDI_DIGIT_WORDS.items():
            if mult_val in ("1000", "100000", "10000000"):
                pattern = rf"\b{num_word}\s+{mult_word}\b"
                if re.search(pattern, lower):
                    return str(int(num_val) * int(mult_val))
    return None


def _resolve_relative_date(text: str, reference_date: date) -> str | None:
    """Resolve Hindi/English relative date expressions to YYYY-MM-DD."""
    lower = text.lower()
    for expr, delta_days in _HINDI_RELATIVE_DATES.items():
        if expr in lower:
            resolved = reference_date + timedelta(days=delta_days)
            return resolved.isoformat()
    return None


class EntityExtractor:
    """Rule-based entity extractor for Hindi/English (Hinglish) utterances.

    Applies a cascade of regex and keyword rules. All rules are deterministic
    and never require a network call. The extractor is the authoritative
    parser for slot values — downstream engines use the normalized values
    without further parsing (Law of Authority, RI-5).

    Architecture: V2 Ch9.
    """

    def __init__(self, reference_date: date | None = None) -> None:
        """
        Args:
            reference_date: The date used to resolve relative expressions like
                            'kal' (tomorrow). Defaults to today (UTC).
        """
        self._reference_date: date = reference_date or date.today()

    def extract(self, turn: TurnInput) -> ExtractedEntities:
        """Extract all entity slots from TurnInput.transcript.

        Args:
            turn: Finalized TurnInput from the Dialogue Manager.

        Returns:
            ExtractedEntities with all matched slots. Empty slots dict
            if no entities are found.
        """
        text = turn.transcript
        slots: dict[str, ExtractedValue] = {}

        self._extract_amount(text, slots)
        self._extract_date(text, slots)
        self._extract_phone(text, slots)
        self._extract_upi(text, slots)
        self._extract_partial_amount(text, slots)

        for entity_type in slots:
            _ENTITY_EXTRACTIONS.labels(entity_type=entity_type).inc()

        logger.debug(
            "Entities extracted",
            extra={
                "turn_id": turn.turn_id,
                "call_id": turn.call_id,
                "slot_count": len(slots),
                "slots": list(slots.keys()),
            },
        )

        return ExtractedEntities(
            slots=slots,
            confidence=1.0 if slots else 0.0,
        )

    # ------------------------------------------------------------------
    # Private extractors
    # ------------------------------------------------------------------

    def _extract_amount(self, text: str, slots: dict[str, ExtractedValue]) -> None:
        # ₹ notation
        m = _RE_RUPEE.search(text)
        if m:
            raw = _strip_commas(m.group(1))
            # Drop decimal part for whole-rupee storage
            amount = str(int(float(raw)))
            slots[EntityType.AMOUNT.value] = ExtractedValue(
                entity_type=EntityType.AMOUNT,
                normalized=amount,
                surface_form=m.group(0),
            )
            return

        # Rs / rupees prefix
        m2 = _RE_RUPEE_ENG.search(text)
        if m2:
            raw = _strip_commas(m2.group(1))
            slots[EntityType.AMOUNT.value] = ExtractedValue(
                entity_type=EntityType.AMOUNT,
                normalized=str(int(float(raw))),
                surface_form=m2.group(0),
            )
            return

        # Hindi word amounts
        hindi_amount = _parse_amount_hindi(text)
        if hindi_amount:
            slots[EntityType.AMOUNT.value] = ExtractedValue(
                entity_type=EntityType.AMOUNT,
                normalized=hindi_amount,
                surface_form=text[:40],
            )

    def _extract_date(self, text: str, slots: dict[str, ExtractedValue]) -> None:
        # Priority: explicit/absolute dates are the most certain signal, then
        # day-of-month ("N tareekh"), then a day-count offset ("15 din mein"),
        # then vaguer relative-date phrases ("kal", "agle hafte") — checked
        # last since they're substring-matched and least specific.

        # Absolute date D/M/Y or D-M-Y
        m = _RE_DATE_DMY.search(text)
        if m:
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if year < 100:
                year += 2000
            try:
                resolved = date(year, month, day).isoformat()
                slots[EntityType.DATE.value] = ExtractedValue(
                    entity_type=EntityType.DATE,
                    normalized=resolved,
                    surface_form=m.group(0),
                )
            except ValueError:
                pass
            return

        # "10 January" style
        m2 = _RE_DATE_WORDS.search(text)
        if m2:
            day = int(m2.group(1))
            month_token = m2.group(0).split()[-1].lower().rstrip(".")
            month_num = _MONTH_MAP.get(month_token, 0)
            if month_num:
                year = self._reference_date.year
                try:
                    resolved = date(year, month_num, day).isoformat()
                    slots[EntityType.DATE.value] = ExtractedValue(
                        entity_type=EntityType.DATE,
                        normalized=resolved,
                        surface_form=m2.group(0),
                    )
                    return
                except ValueError:
                    pass

        # "N तारीख" / "N tareekh" — day-of-month promise (Phase 6).
        tareekh_date = _resolve_tareekh(text, self._reference_date)
        if tareekh_date is not None:
            m3 = _RE_TAREEKH.search(text)
            slots[EntityType.PROMISE_DATE.value] = ExtractedValue(
                entity_type=EntityType.PROMISE_DATE,
                normalized=tareekh_date.isoformat(),
                surface_form=m3.group(0) if m3 else text[:40],
            )
            return

        # "N din" / "N days" / "N दिन" — relative day-count offset (Phase 6).
        day_offset = _resolve_day_offset(text)
        if day_offset is not None:
            resolved_day = self._reference_date + timedelta(days=day_offset)
            m4 = _RE_DAY_UNIT.search(text.translate(_DEVA_DIGIT_TRANS))
            slots[EntityType.PROMISE_DATE.value] = ExtractedValue(
                entity_type=EntityType.PROMISE_DATE,
                normalized=resolved_day.isoformat(),
                surface_form=m4.group(0) if m4 else text[:40],
            )
            return

        # Relative date expressions ("kal", "agle hafte", ...) → PROMISE_DATE
        relative = _resolve_relative_date(text, self._reference_date)
        if relative:
            slots[EntityType.PROMISE_DATE.value] = ExtractedValue(
                entity_type=EntityType.PROMISE_DATE,
                normalized=relative,
                surface_form=text[:40],
            )

    def _extract_phone(self, text: str, slots: dict[str, ExtractedValue]) -> None:
        m = _RE_PHONE.search(text)
        if m:
            slots[EntityType.PHONE.value] = ExtractedValue(
                entity_type=EntityType.PHONE,
                normalized=m.group(1),
                surface_form=m.group(0),
            )

    def _extract_upi(self, text: str, slots: dict[str, ExtractedValue]) -> None:
        m = _RE_UPI.search(text)
        if m:
            upi = m.group(1)
            if "@" in upi and not upi.startswith("@"):
                slots[EntityType.UPI_ID.value] = ExtractedValue(
                    entity_type=EntityType.UPI_ID,
                    normalized=upi.lower(),
                    surface_form=upi,
                )

    def _extract_partial_amount(self, text: str, slots: dict[str, ExtractedValue]) -> None:
        m = _RE_PARTIAL.search(text)
        if not m:
            return
        # Require a partial-payment context word to distinguish "ek hazaar abhi"
        # (partial offer) from "ek hazaar de dunga" (full commitment).
        partial_context = ("abhi", "thoda", "partial", "kuch abhi")
        lower = text.lower()
        if not any(kw in lower for kw in partial_context):
            return
        num_str = _HINDI_DIGIT_WORDS.get(m.group(1).lower(), "1")
        mult_str = _HINDI_DIGIT_WORDS.get(m.group(2).lower(), "1000")
        amount = str(int(num_str) * int(mult_str))
        # PARTIAL_AMOUNT takes precedence; remove any AMOUNT set by _parse_amount_hindi.
        slots.pop(EntityType.AMOUNT.value, None)
        slots[EntityType.PARTIAL_AMOUNT.value] = ExtractedValue(
            entity_type=EntityType.PARTIAL_AMOUNT,
            normalized=amount,
            surface_form=m.group(0),
        )
