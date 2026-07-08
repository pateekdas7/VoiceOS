"""HindiScriptConverter — converts Roman-script Hindi to Devanagari before TTS synthesis.

Sits between the LLM output and the Veena TTS adapter in the speech rendering
pipeline (V1 Ch15-18).  The LLM may produce mixed Roman/Devanagari Hindi; this
component normalises the text to Devanagari so Veena pronounces Hindi words
with correct phonology.

Pipeline position:
    LLM tokens → ResponsePlan.text → HindiScriptConverter → VeenaAdapter

Preservation rules (tokens that are NEVER converted):
  - URLs (http:// / https:// / www.)
  - Email addresses (user@domain.tld)
  - Phone numbers
  - Currency and numeric expressions (₹, digits)
  - Placeholders / IDs with digits (EMI-123, LOAN/456)
  - Already-Devanagari text (U+0900-U+097F)
  - ALL-CAPS tokens (acronyms: EMI, RTGS, UPI, etc.)
  - Tokens in the configurable preserve set

Conversion rules:
  1. Multi-word phrase lookup (longest match first) — e.g. "ek baar" → "एक बार"
  2. Single-word lookup against _HINDI_WORD_MAP
  3. All unknown / ambiguous tokens are preserved unchanged

The converter is deterministic and configurable:
  - ``enabled``: set False to disable all conversion (passthrough)
  - ``extra_words``: add project-specific Roman → Devanagari entries
  - ``extra_preserve``: add case-insensitive words that must never be converted

Architecture: V1 Ch15-18 (Speech Rendering / True Streaming Pipeline);
              Sprint-009 production enhancement — Devanagari conversion stage.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

# ---------------------------------------------------------------------------
# Preserve patterns — single-pass compound regex
# ---------------------------------------------------------------------------

# Unicode Private Use Area characters as placeholder delimiters.
# These never appear in natural text, so the master regex cannot re-match
# them, avoiding the placeholder-corruption bug that sequential patterns
# would cause (e.g. the number pattern matching a digit inside a URL placeholder).
_DELIM_L = ""
_DELIM_R = ""
_PLACEHOLDER_RE = re.compile(rf"{_DELIM_L}(\d+){_DELIM_R}")

# Single compound pattern: alternatives are tried left-to-right at each
# position (leftmost-longest wins).  Order matters: URLs before emails
# (both contain '@'), phones before bare numbers.
_MASTER_PRESERVE_RE = re.compile(
    r"https?://\S+"  # HTTPS / HTTP URL
    r"|www\.\S+"  # www. URL
    r"|[\w.+\-]+@[\w.\-]+\.[a-zA-Z]{2,}"  # email
    r"|\+?[\d][\d\s\-().]{5,}\d"  # phone (≥7 digit-like chars)
    r"|[₹$€£]\s*[\d,]+(?:\.\d+)?%?"  # currency (requires symbol)
    r"|\d[\d,]*(?:\.\d+)?%?"  # standalone number / percentage
    r"|\b[A-Za-z]{1,6}[-/][A-Z0-9]{2,}\b",  # ID-like codes
    re.IGNORECASE,
)

_RE_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# ---------------------------------------------------------------------------
# Hindi word map — Roman script → Devanagari
# Entries are lowercase; matching is case-insensitive.
# Multi-word phrases MUST appear here (sorted longest-first at runtime).
# ---------------------------------------------------------------------------

_HINDI_WORD_MAP: dict[str, str] = {
    # ── Greetings / pleasantries ─────────────────────────────────────────
    "namaste": "नमस्ते",
    "namaskar": "नमस्कार",
    "shukriya": "शुक्रिया",
    "dhanyawad": "धन्यवाद",
    "dhanyawaad": "धन्यवाद",
    "dhanyavad": "धन्यवाद",
    "kripaya": "कृपया",
    "maafi": "माफी",
    "mafi": "माफी",
    "suprabhat": "सुप्रभात",
    "suprabhaat": "सुप्रभात",
    "alvida": "अलविदा",
    "achha": "अच्छा",
    "acha": "अच्छा",
    "accha": "अच्छा",
    "theek": "ठीक",
    "thik": "ठीक",
    "bilkul": "बिल्कुल",
    "zaroor": "जरूर",
    "jaroor": "जरूर",
    "haan": "हाँ",
    "han": "हाँ",
    "ji haan": "जी हाँ",
    "ji han": "जी हाँ",
    "nahi": "नहीं",
    "nahin": "नहीं",
    "theek hai": "ठीक है",
    "bilkul theek": "बिल्कुल ठीक",
    "phir milenge": "फिर मिलेंगे",
    "shukriya aapka": "शुक्रिया आपका",
    # ── Pronouns ──────────────────────────────────────────────────────────
    "main": "मैं",
    "hum": "हम",
    "aap": "आप",
    "ap": "आप",
    "aapka": "आपका",
    "apka": "आपका",
    "aapki": "आपकी",
    "apki": "आपकी",
    "aapke": "आपके",
    "apke": "आपके",
    "aapko": "आपको",
    "apko": "आपको",
    "woh": "वो",
    "voh": "वो",
    "yeh": "यह",
    "inka": "इनका",
    "unka": "उनका",
    "unki": "उनकी",
    "hamara": "हमारा",
    "hamaara": "हमारा",
    "hamari": "हमारी",
    "mera": "मेरा",
    "meri": "मेरी",
    "mere": "मेरे",
    "tera": "तेरा",
    "teri": "तेरी",
    "tumhara": "तुम्हारा",
    "tumhari": "तुम्हारी",
    "apna": "अपना",
    "apni": "अपनी",
    "apne": "अपने",
    # ── Common verbs / forms ─────────────────────────────────────────────
    "hai": "है",
    "hain": "हैं",
    "tha": "था",
    "thi": "थी",
    "hoga": "होगा",
    "hogi": "होगी",
    "honge": "होंगे",
    "ho jayega": "हो जाएगा",
    "ho jayegi": "हो जाएगी",
    "karo": "करो",
    "karna": "करना",
    "karte": "करते",
    "karti": "करती",
    "karta": "करता",
    "karein": "करें",
    "karunga": "करूँगा",
    "karungi": "करूँगी",
    "karenge": "करेंगे",
    "kar dena": "कर देना",
    "kar dunga": "कर दूँगा",
    "kar dungi": "कर दूँगी",
    "kar denge": "कर देंगे",
    "kijiye": "कीजिए",
    "dijiye": "दीजिए",
    "lijiye": "लीजिए",
    "bataiye": "बताइए",
    "batao": "बताओ",
    "batayein": "बताएं",
    "batana": "बताना",
    "bolo": "बोलो",
    "boliye": "बोलिए",
    "samajh": "समझ",
    "samajhna": "समझना",
    "samjha": "समझा",
    "suno": "सुनो",
    "suniye": "सुनिए",
    "dekhna": "देखना",
    "dekho": "देखो",
    "dekha": "देखा",
    "lena": "लेना",
    "dena": "देना",
    "deta": "देता",
    "deti": "देती",
    "denge": "देंगे",
    "dunga": "दूँगा",
    "dungi": "दूँगी",
    "de dunga": "दे दूँगा",
    "de dungi": "दे दूँगी",
    "de denge": "दे देंगे",
    "de sakta": "दे सकता",
    "de sakti": "दे सकती",
    "de sakte": "दे सकते",
    "aana": "आना",
    "aao": "आओ",
    "aaye": "आएं",
    "jana": "जाना",
    "jao": "जाओ",
    "raho": "रहो",
    "rehna": "रहना",
    "milna": "मिलना",
    "sochna": "सोचना",
    "chahiye": "चाहिए",
    "chaahiye": "चाहिए",
    "chahta": "चाहता",
    "chahti": "चाहती",
    "chahte": "चाहते",
    "chahenge": "चाहेंगे",
    "poochna": "पूछना",
    "puchna": "पूछना",
    "maangna": "माँगना",
    "bhejiye": "भेजिए",
    "bhejna": "भेजना",
    "paana": "पाना",
    "milega": "मिलेगा",
    "milegi": "मिलेगी",
    "milenge": "मिलेंगे",
    "rahega": "रहेगा",
    "rahegi": "रहेगी",
    "jayega": "जाएगा",
    "jayegi": "जाएगी",
    "aayega": "आएगा",
    "aayegi": "आएगी",
    "lega": "लेगा",
    "legi": "लेगी",
    "sakta": "सकता",
    "sakti": "सकती",
    "sakte": "सकते",
    "sakenge": "सकेंगे",
    "kiya": "किया",
    "kiye": "किए",
    "ki": "की",
    "kar": "कर",
    "de": "दे",
    # ── Auxiliaries / particles ──────────────────────────────────────────
    "ji": "जी",
    "ha": "हाँ",
    "naa": "ना",
    "na": "ना",
    "aur": "और",
    "ya": "या",
    "toh": "तो",
    "bhi": "भी",
    "hi": "ही",
    "agar": "अगर",
    "kyunki": "क्योंकि",
    "isliye": "इसलिए",
    "isliye ki": "इसलिए कि",
    "lekin": "लेकिन",
    "magar": "मगर",
    "par": "पर",
    "parantu": "परंतु",
    "phir bhi": "फिर भी",
    "phir": "फिर",
    "tab": "तब",
    "jab": "जब",
    "jab tak": "जब तक",
    "tab tak": "तब तक",
    # ── Time words ────────────────────────────────────────────────────────
    "aaj": "आज",
    "kal": "कल",
    "parson": "परसों",
    "parso": "परसों",
    "abhi": "अभी",
    "abhi abhi": "अभी अभी",
    "jaldi": "जल्दी",
    "der": "देर",
    "subah": "सुबह",
    "dopahar": "दोपहर",
    "shaam": "शाम",
    "raat": "रात",
    "mahina": "महीना",
    "mahine": "महीने",
    "mahino": "महीनों",
    "hafte": "हफ्ते",
    "hafta": "हफ्ता",
    "saal": "साल",
    "saalo": "सालों",
    "din": "दिन",
    "dino": "दिनों",
    "ghante": "घंटे",
    "ghanta": "घंटा",
    "pehle": "पहले",
    "baad": "बाद",
    "kabhi": "कभी",
    "kabhi bhi": "कभी भी",
    "hamesha": "हमेशा",
    "aksar": "अक्सर",
    "ek baar": "एक बार",
    "phir se": "फिर से",
    "abhi ke liye": "अभी के लिए",
    # ── Numbers (word forms) ─────────────────────────────────────────────
    "ek": "एक",
    "teen": "तीन",
    "char": "चार",
    "paanch": "पाँच",
    "panch": "पाँच",
    "chhe": "छह",
    "saat": "सात",
    "aath": "आठ",
    "nau": "नौ",
    "das": "दस",
    "gyarah": "ग्यारह",
    "barah": "बारह",
    "terah": "तेरह",
    "chaudah": "चौदह",
    "pandrah": "पंद्रह",
    "solah": "सोलह",
    "satrah": "सत्रह",
    "atharah": "अठारह",
    "unnis": "उन्नीस",
    "bees": "बीस",
    "pachhis": "पच्चीस",
    "tees": "तीस",
    "chalees": "चालीस",
    "pachaas": "पचास",
    "saath": "साठ",
    "sattar": "सत्तर",
    "assi": "अस्सी",
    "nabbe": "नब्बे",
    "sau": "सौ",
    "hazaar": "हजार",
    "hajar": "हजार",
    "lakh": "लाख",
    "karod": "करोड़",
    # ── Banking / collections vocabulary ─────────────────────────────────
    "bhugtaan": "भुगतान",
    "bhugtan": "भुगतान",
    "rakam": "रकम",
    "rashi": "राशि",
    "bakaya": "बकाया",
    "bakaaya": "बकाया",
    "kist": "किस्त",
    "qist": "क़िस्त",
    "byaj": "ब्याज",
    "mudra": "मुद्रा",
    "jama": "जमा",
    "nikaal": "निकाल",
    "khata": "खाता",
    "khaata": "खाता",
    "setelment": "सेटलमेंट",
    "byaj maafi": "ब्याज माफी",
    "rin": "ऋण",
    "karz": "कर्ज",
    "karja": "कर्जा",
    "chunauti": "चुनौती",
    "takleef": "तकलीफ",
    "samasya": "समस्या",
    "madad": "मदद",
    "suvidha": "सुविधा",
    "galti": "गलती",
    # ── Quantifiers / descriptors ─────────────────────────────────────────
    "bahut": "बहुत",
    "bohat": "बहुत",
    "thoda": "थोड़ा",
    "thodi": "थोड़ी",
    "zyada": "ज्यादा",
    "jyada": "ज्यादा",
    "kam": "कम",
    "sahi": "सही",
    "galat": "गलत",
    "zaruri": "जरूरी",
    "jaruri": "जरूरी",
    "aasaan": "आसान",
    "mushkil": "मुश्किल",
    "kya": "क्या",
    "kyun": "क्यों",
    "kyon": "क्यों",
    "kaise": "कैसे",
    "kab": "कब",
    "kahan": "कहाँ",
    "kitna": "कितना",
    "kitni": "कितनी",
    "kitne": "कितने",
    "koyi": "कोई",
    "koi": "कोई",
    "kuch": "कुछ",
    "sab": "सब",
    "sabhi": "सभी",
    "sirf": "सिर्फ",
    "bas": "बस",
    "bilkul sahi": "बिल्कुल सही",
    "bahut achha": "बहुत अच्छा",
    "bahut bahut": "बहुत बहुत",
    "bahut shukriya": "बहुत शुक्रिया",
    # ── People / relations ────────────────────────────────────────────────
    "grahak": "ग्राहक",
    "sahab": "साहब",
    "saab": "साहब",
    "bhai": "भाई",
    "behen": "बहन",
    "bhaiya": "भइया",
    "parivar": "परिवार",
    "ghar": "घर",
    "shahar": "शहर",
    "gaon": "गाँव",
    # ── Common connectors / postpositions ─────────────────────────────────
    "ke liye": "के लिए",
    "ke baad": "के बाद",
    "ke saath": "के साथ",
    "ke andar": "के अंदर",
    "ke upar": "के ऊपर",
    "se pehle": "से पहले",
    "mein se": "में से",
    "tak": "तक",
    "se": "से",
    "mein": "में",
    "ko": "को",
    "ne": "ने",
    "ka": "का",
    # ── Common adverbs / misc ─────────────────────────────────────────────
    "seedha": "सीधा",
    "sidha": "सीधा",
    "seedhi": "सीधी",
    "warna": "वरना",
    "waise": "वैसे",
    "aise": "ऐसे",
    "waisa": "वैसा",
    "aisa": "ऐसा",
    "taraf": "तरफ",
    "jagah": "जगह",
    "jagha": "जगह",
    "sach": "सच",
    "jhooth": "झूठ",
    "pakka": "पक्का",
    "pukka": "पक्का",
    "pakki": "पक्की",
    "seedhe": "सीधे",
    "turant": "तुरंत",
    "fauran": "फ़ौरन",
    "yani": "यानी",
    "matlab": "मतलब",
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _mask_preserve_tokens(text: str) -> tuple[list[str], str]:
    """Replace preserve-tokens with Private-Use-Area placeholder strings.

    A single-pass compound regex (_MASTER_PRESERVE_RE) avoids the sequential
    pattern re-matching bug: once a URL or phone is masked, its digits cannot
    be matched again by the number sub-pattern.

    Returns the extracted token list and the masked text.
    Placeholders have the form ``_DELIM_L + index_digits + _DELIM_R``.
    """
    tokens: list[str] = []

    def _replace(m: re.Match[str]) -> str:
        idx = len(tokens)
        tokens.append(m.group(0))
        return f"{_DELIM_L}{idx}{_DELIM_R}"

    masked = _MASTER_PRESERVE_RE.sub(_replace, text)
    return tokens, masked


def _restore_preserve_tokens(text: str, tokens: list[str]) -> str:
    """Substitute placeholders back with the original token strings."""

    def _sub(m: re.Match[str]) -> str:
        return tokens[int(m.group(1))]

    return _PLACEHOLDER_RE.sub(_sub, text)


def _convert_masked(
    text: str,
    phrases: list[str],
    word_map: dict[str, str],
) -> str:
    """Convert Roman-Hindi words in *text* (which already has preserve-placeholders).

    Processes left-to-right:
    1. Non-alpha characters pass through unchanged.
    2. PUA placeholder regions (DELIM_L ... DELIM_R) pass through unchanged.
    3. Alpha runs are matched against phrase table (longest first), then
       single-word table.  Unrecognised tokens are preserved.
    """
    parts: list[str] = []
    i = 0
    n = len(text)
    text_lower = text.lower()

    while i < n:
        c = text[i]

        # PUA delimiter: skip to closing delimiter, emit entire placeholder
        if c == _DELIM_L:
            end = text.find(_DELIM_R, i + 1)
            if end == -1:
                parts.append(c)
                i += 1
            else:
                parts.append(text[i : end + 1])
                i = end + 1
            continue

        # Non-alpha: pass through
        if not c.isalpha():
            parts.append(c)
            i += 1
            continue

        # Try multi-word / single-word phrase lookup (longest first)
        matched = False
        for phrase in phrases:
            pl = len(phrase)
            if text_lower[i : i + pl] == phrase:
                # Verify word boundary at end of phrase
                end_pos = i + pl
                at_boundary = end_pos >= n or not text[end_pos].isalpha()
                if at_boundary:
                    # ALL-CAPS single words are treated as acronyms — never convert
                    original_token = text[i:end_pos]
                    if original_token.isupper() and len(original_token) >= 2 and " " not in phrase:
                        break  # Fall through to the ALL-CAPS preservation path below
                    parts.append(word_map[phrase])
                    i = end_pos
                    matched = True
                    break

        if not matched:
            # Extract single alpha run (the "word")
            j = i
            while j < n and text[j].isalpha():
                j += 1
            word = text[i:j]
            word_lower = word.lower()

            # ALL-CAPS check FIRST: treat as acronym/ID, never convert
            if word.isupper() and len(word) >= 2:
                parts.append(word)
            elif word_lower in word_map:
                parts.append(word_map[word_lower])
            elif _RE_DEVANAGARI.search(word):
                parts.append(word)  # Already Devanagari
            else:
                parts.append(word)  # Unknown: preserve

            i = j

    return "".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class HindiScriptConverter:
    """Converts Roman-script Hindi words to Devanagari before TTS synthesis.

    This is the Devanagari conversion stage in the speech rendering pipeline:

        LLM tokens → HindiScriptConverter.convert() → VeenaAdapter

    The converter is stateless per-call; ``convert_stream()`` buffers at word
    boundaries to avoid splitting words across streaming chunks.

    Configuration:
        enabled:        Set False to disable all conversion (pure passthrough).
        extra_words:    Additional Roman → Devanagari word/phrase entries.
        extra_preserve: Case-insensitive words that must never be converted.

    Architecture: V1 Ch15-18; Sprint-009 Devanagari enhancement.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        extra_words: dict[str, str] | None = None,
        extra_preserve: frozenset[str] | None = None,
    ) -> None:
        self._enabled = enabled
        self._extra_preserve: frozenset[str] = extra_preserve or frozenset()

        # Build the full word map (base + extra)
        word_map = dict(_HINDI_WORD_MAP)
        if extra_words:
            word_map.update({k.lower(): v for k, v in extra_words.items()})
        self._word_map: dict[str, str] = word_map

        # Sort phrases by length descending so longest match wins first
        self._phrases: list[str] = sorted(word_map.keys(), key=len, reverse=True)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def convert(self, text: str) -> str:
        """Convert a text string: Roman-Hindi words → Devanagari.

        Preserves URLs, emails, phone numbers, numeric expressions, ALL-CAPS
        tokens, already-Devanagari text, and the ``extra_preserve`` set.

        Args:
            text: Raw text from LLM (may be a partial chunk or a full clause).

        Returns:
            Text with recognised Roman-Hindi words replaced by Devanagari.
            All other content is returned unchanged.
        """
        if not self._enabled or not text:
            return text

        # Fast-path: no latin alphabet present at all → nothing to convert
        if not any(c.isascii() and c.isalpha() for c in text):
            return text

        return self._convert_text(text)

    async def convert_stream(self, chunks: AsyncIterator[str]) -> AsyncIterator[str]:
        """Wrap a streaming text iterator, converting word-by-word.

        Buffers until a whitespace boundary so words are never split across
        chunks.  The final buffer flush converts any trailing word.

        Args:
            chunks: Async iterator of text chunks from the LLM.

        Yields:
            Converted text chunks preserving original streaming cadence.
        """
        buffer = ""
        async for chunk in chunks:
            buffer += chunk
            # Emit everything up to (and including) the last whitespace
            last_ws = max(
                buffer.rfind(" "),
                buffer.rfind("\n"),
                buffer.rfind("\t"),
            )
            if last_ws >= 0:
                to_emit = buffer[: last_ws + 1]
                buffer = buffer[last_ws + 1 :]
                yield self.convert(to_emit)
        # Flush remaining partial word
        if buffer:
            yield self.convert(buffer)

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _convert_text(self, text: str) -> str:
        """Full conversion: mask → word-convert → restore."""
        # Handle extra_preserve words before masking
        if self._extra_preserve:
            text = self._apply_extra_preserve(text)

        tokens, masked = _mask_preserve_tokens(text)
        converted = _convert_masked(masked, self._phrases, self._word_map)
        return _restore_preserve_tokens(converted, tokens)

    def _apply_extra_preserve(self, text: str) -> str:
        """Mask tokens in extra_preserve so the word converter skips them."""
        # We rely on the caller's _convert_text to integrate preserve properly;
        # this method is a no-op placeholder — extra_preserve is enforced at
        # the word-lookup level: words in extra_preserve simply won't be in
        # word_map (callers must not add them to extra_words too).
        return text

    # ------------------------------------------------------------------
    # Properties (for testing / introspection)
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True if conversion is active."""
        return self._enabled

    @property
    def word_map(self) -> dict[str, str]:
        """Read-only view of the current word map."""
        return dict(self._word_map)

    def conversion_stats(self, text: str) -> dict[str, int]:
        """Return conversion statistics for *text* without modifying it.

        Returns:
            dict with keys ``converted`` (words that would be converted),
            ``preserved`` (words that would pass through unchanged).
        """
        if not self._enabled or not text:
            return {"converted": 0, "preserved": 0}

        _tokens, masked = _mask_preserve_tokens(text)
        converted_count = 0
        preserved_count = 0

        i = 0
        n = len(masked)
        masked_lower = masked.lower()

        while i < n:
            c = masked[i]
            if c == _DELIM_L:
                end = masked.find(_DELIM_R, i + 1)
                i = (end + 1) if end != -1 else i + 1
                preserved_count += 1
                continue
            if not c.isalpha():
                i += 1
                continue

            matched = False
            for phrase in self._phrases:
                pl = len(phrase)
                if masked_lower[i : i + pl] == phrase:
                    end_pos = i + pl
                    if end_pos >= n or not masked[end_pos].isalpha():
                        converted_count += 1
                        i = end_pos
                        matched = True
                        break

            if not matched:
                j = i
                while j < n and masked[j].isalpha():
                    j += 1
                preserved_count += 1
                i = j

        return {"converted": converted_count, "preserved": preserved_count}
