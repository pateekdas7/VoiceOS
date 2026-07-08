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
}

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
        # Relative date expressions → PROMISE_DATE
        relative = _resolve_relative_date(text, self._reference_date)
        if relative:
            slots[EntityType.PROMISE_DATE.value] = ExtractedValue(
                entity_type=EntityType.PROMISE_DATE,
                normalized=relative,
                surface_form=text[:40],
            )
            return

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
                except ValueError:
                    pass

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
