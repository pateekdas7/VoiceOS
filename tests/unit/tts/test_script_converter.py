"""Comprehensive unit tests for HindiScriptConverter.

Tests cover:
  - Hindi transliteration → Devanagari (single words, phrases)
  - Mixed Hindi + English preservation
  - English-only passthrough
  - Numbers, currency, dates (preserved)
  - Phone numbers (preserved)
  - URLs (preserved)
  - Email addresses (preserved)
  - Abbreviations / ALL-CAPS (preserved)
  - Punctuation preservation
  - Unicode correctness
  - Long responses
  - Streaming (convert_stream)
  - Edge cases (empty, whitespace, already-Devanagari)
  - Configuration (enabled=False, extra_words, extra_preserve)
  - Banking / collections domain vocabulary
  - conversion_stats() helper

Architecture: Sprint-009 Devanagari enhancement; V1 Ch15-18.
"""

from __future__ import annotations

import unicodedata
from collections.abc import AsyncIterator

import pytest

from src.services.tts.script_converter import _DELIM_L, HindiScriptConverter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def converter() -> HindiScriptConverter:
    return HindiScriptConverter()


async def _chunks(*texts: str) -> AsyncIterator[str]:
    for t in texts:
        yield t


async def collect_stream(gen: AsyncIterator[str]) -> str:
    """Collect all yielded strings from an async generator."""
    parts: list[str] = []
    async for chunk in gen:
        parts.append(chunk)
    return "".join(parts)


def is_valid_unicode(text: str) -> bool:
    """Return True if text contains no ill-formed Unicode sequences."""
    try:
        text.encode("utf-8").decode("utf-8")
        return True
    except (UnicodeEncodeError, UnicodeDecodeError):
        return False


def has_devanagari(text: str) -> bool:
    """Return True if text contains at least one Devanagari codepoint."""
    return any("ऀ" <= ch <= "ॿ" for ch in text)


# ---------------------------------------------------------------------------
# Basic word conversion — Greetings
# ---------------------------------------------------------------------------


def test_namaste_converts_to_devanagari() -> None:
    """'namaste' is converted to नमस्ते."""
    c = converter()
    result = c.convert("namaste")
    assert result == "नमस्ते"


def test_shukriya_converts() -> None:
    """'shukriya' → शुक्रिया"""
    c = converter()
    assert c.convert("shukriya") == "शुक्रिया"


def test_kripaya_converts() -> None:
    """'kripaya' → कृपया"""
    c = converter()
    assert c.convert("kripaya") == "कृपया"


def test_haan_converts() -> None:
    """'haan' (yes) → हाँ"""
    c = converter()
    assert c.convert("haan") == "हाँ"


def test_nahi_converts() -> None:
    """'nahi' (no) → नहीं"""
    c = converter()
    assert c.convert("nahi") == "नहीं"


# ---------------------------------------------------------------------------
# Basic word conversion — Common words
# ---------------------------------------------------------------------------


def test_aap_converts() -> None:
    """'aap' (you) → आप"""
    c = converter()
    assert c.convert("aap") == "आप"


def test_aapka_converts() -> None:
    """'aapka' (your) → आपका"""
    c = converter()
    assert c.convert("aapka") == "आपका"


def test_hai_converts() -> None:
    """'hai' (is) → है"""
    c = converter()
    assert c.convert("hai") == "है"


def test_theek_converts() -> None:
    """'theek' (okay) → ठीक"""
    c = converter()
    assert c.convert("theek") == "ठीक"


def test_kya_converts() -> None:
    """'kya' (what) → क्या"""
    c = converter()
    assert c.convert("kya") == "क्या"


def test_abhi_converts() -> None:
    """'abhi' (right now) → अभी"""
    c = converter()
    assert c.convert("abhi") == "अभी"


def test_kal_converts() -> None:
    """'kal' (tomorrow) → कल"""
    c = converter()
    assert c.convert("kal") == "कल"


def test_aaj_converts() -> None:
    """'aaj' (today) → आज"""
    c = converter()
    assert c.convert("aaj") == "आज"


# ---------------------------------------------------------------------------
# Banking / collections vocabulary
# ---------------------------------------------------------------------------


def test_bakaya_converts() -> None:
    """'bakaya' (outstanding balance) → बकाया"""
    c = converter()
    assert c.convert("bakaya") == "बकाया"


def test_bhugtaan_converts() -> None:
    """'bhugtaan' (payment) → भुगतान"""
    c = converter()
    assert c.convert("bhugtaan") == "भुगतान"


def test_kist_converts() -> None:
    """'kist' (instalment) → किस्त"""
    c = converter()
    assert c.convert("kist") == "किस्त"


def test_hazaar_converts() -> None:
    """'hazaar' (thousand) → हजार"""
    c = converter()
    assert c.convert("hazaar") == "हजार"


def test_lakh_converts() -> None:
    """'lakh' → लाख"""
    c = converter()
    assert c.convert("lakh") == "लाख"


def test_khata_converts() -> None:
    """'khata' (account) → खाता"""
    c = converter()
    assert c.convert("khata") == "खाता"


def test_dunga_converts() -> None:
    """'dunga' (will give) → दूँगा"""
    c = converter()
    assert c.convert("dunga") == "दूँगा"


def test_de_dunga_phrase_converts() -> None:
    """Multi-word phrase 'de dunga' → दे दूँगा"""
    c = converter()
    assert c.convert("de dunga") == "दे दूँगा"


# ---------------------------------------------------------------------------
# Multi-word phrase lookup
# ---------------------------------------------------------------------------


def test_ji_haan_phrase_converts() -> None:
    """'ji haan' (yes sir) → जी हाँ"""
    c = converter()
    assert c.convert("ji haan") == "जी हाँ"


def test_theek_hai_phrase_converts() -> None:
    """'theek hai' → ठीक है"""
    c = converter()
    assert c.convert("theek hai") == "ठीक है"


def test_ek_baar_phrase_converts() -> None:
    """'ek baar' → एक बार"""
    c = converter()
    assert c.convert("ek baar") == "एक बार"


def test_phir_se_phrase_converts() -> None:
    """'phir se' → फिर से"""
    c = converter()
    assert c.convert("phir se") == "फिर से"


def test_bahut_shukriya_phrase_converts() -> None:
    """'bahut shukriya' → बहुत शुक्रिया"""
    c = converter()
    assert c.convert("bahut shukriya") == "बहुत शुक्रिया"


# ---------------------------------------------------------------------------
# Mixed Hindi + English
# ---------------------------------------------------------------------------


def test_mixed_hindi_english_preserves_english_words() -> None:
    """English words in a mixed sentence remain unchanged."""
    c = converter()
    result = c.convert("aapka loan amount hai 50000")
    # 'aapka' → आपका, 'hai' → है; 'loan' and 'amount' are NOT in word map → preserved
    assert "आपका" in result
    assert "है" in result
    assert "loan" in result
    assert "amount" in result
    assert "50000" in result


def test_mixed_devanagari_roman_passthrough() -> None:
    """Already-Devanagari words are not double-converted."""
    c = converter()
    result = c.convert("नमस्ते aap kaise hain")
    assert "नमस्ते" in result
    assert "आप" in result
    assert has_devanagari(result)


def test_english_greeting_preserved() -> None:
    """Common English words like 'hello' are NOT converted (not in Hindi map)."""
    c = converter()
    result = c.convert("hello sir")
    assert "hello" in result
    assert "sir" in result


# ---------------------------------------------------------------------------
# English-only passthrough
# ---------------------------------------------------------------------------


def test_english_sentence_unchanged() -> None:
    """A purely English sentence passes through without modification."""
    c = converter()
    text = "We understand your situation and we can help you."
    result = c.convert(text)
    assert result == text


def test_english_only_no_devanagari_introduced() -> None:
    """No Devanagari characters appear in an English-only string."""
    c = converter()
    result = c.convert("Your payment is due on Monday.")
    assert not has_devanagari(result)


# ---------------------------------------------------------------------------
# Numbers and currency (must be preserved)
# ---------------------------------------------------------------------------


def test_integer_preserved() -> None:
    """Plain integers pass through unchanged."""
    c = converter()
    assert c.convert("50000") == "50000"


def test_currency_rupees_preserved() -> None:
    """₹ currency amounts are preserved."""
    c = converter()
    result = c.convert("aapka bakaya ₹1,50,000 hai")
    assert "₹1,50,000" in result
    assert "बकाया" in result


def test_decimal_number_preserved() -> None:
    """Decimal numbers pass through unchanged."""
    c = converter()
    result = c.convert("interest rate 8.5%")
    assert "8.5%" in result


def test_number_in_sentence_preserved() -> None:
    """Numbers embedded in a Hindi sentence are not altered."""
    c = converter()
    result = c.convert("aapka bakaya 75000 rupaye hai")
    assert "75000" in result
    assert "बकाया" in result


def test_comma_separated_number_preserved() -> None:
    """Comma-grouped numbers (Indian format) pass through."""
    c = converter()
    result = c.convert("ek lakh 1,00,000 rupaye")
    assert "1,00,000" in result


# ---------------------------------------------------------------------------
# Phone numbers (must be preserved)
# ---------------------------------------------------------------------------


def test_phone_number_preserved() -> None:
    """A phone number is not modified."""
    c = converter()
    result = c.convert("aap hume 9876543210 par call karein")
    assert "9876543210" in result


def test_formatted_phone_preserved() -> None:
    """Formatted phone number passes through."""
    c = converter()
    result = c.convert("call karein +91-98765-43210")
    assert "+91-98765-43210" in result


# ---------------------------------------------------------------------------
# URLs (must be preserved)
# ---------------------------------------------------------------------------


def test_https_url_preserved() -> None:
    """HTTPS URLs are not modified."""
    c = converter()
    url = "https://pay.example.com/loan"
    result = c.convert(f"payment link hai {url}")
    assert url in result


def test_http_url_preserved() -> None:
    """HTTP URLs pass through unchanged."""
    c = converter()
    url = "http://voiceos.ai/help"
    result = c.convert(f"visit karo {url}")
    assert url in result


def test_www_url_preserved() -> None:
    """www. URLs pass through unchanged."""
    c = converter()
    url = "www.example.com"
    result = c.convert(f"website {url} hai")
    assert url in result


# ---------------------------------------------------------------------------
# Email addresses (must be preserved)
# ---------------------------------------------------------------------------


def test_email_preserved() -> None:
    """Email addresses are not modified."""
    c = converter()
    email = "support@voiceos.ai"
    result = c.convert(f"email karo {email} par")
    assert email in result


def test_email_with_dots_preserved() -> None:
    """Email addresses with dots in local part are preserved."""
    c = converter()
    email = "user.name+tag@company.org"
    result = c.convert(f"apna email hai {email}")
    assert email in result


# ---------------------------------------------------------------------------
# Abbreviations / ALL-CAPS (must be preserved)
# ---------------------------------------------------------------------------


def test_allcaps_emi_preserved() -> None:
    """ALL-CAPS 'EMI' must not be converted."""
    c = converter()
    result = c.convert("aapki EMI miss ho gayi")
    assert "EMI" in result


def test_allcaps_rtgs_preserved() -> None:
    """ALL-CAPS 'RTGS' must not be converted."""
    c = converter()
    result = c.convert("payment karein RTGS se")
    assert "RTGS" in result


def test_allcaps_upi_preserved() -> None:
    """ALL-CAPS 'UPI' must not be converted."""
    c = converter()
    result = c.convert("UPI se bhugtan karein")
    assert "UPI" in result


def test_allcaps_neft_preserved() -> None:
    """ALL-CAPS 'NEFT' must not be converted."""
    c = converter()
    result = c.convert("NEFT transfer karo")
    assert "NEFT" in result


# ---------------------------------------------------------------------------
# Punctuation preservation
# ---------------------------------------------------------------------------


def test_comma_preserved() -> None:
    """Commas in text pass through unchanged."""
    c = converter()
    result = c.convert("haan, theek hai, koi baat nahi")
    assert "," in result


def test_period_preserved() -> None:
    """Periods in text pass through unchanged."""
    c = converter()
    result = c.convert("aapka bhugtan ho jayega. kripaya wait karein.")
    assert ". " in result


def test_question_mark_preserved() -> None:
    """Question marks pass through unchanged."""
    c = converter()
    result = c.convert("aap kya kar sakte hain?")
    assert "?" in result


def test_exclamation_preserved() -> None:
    """Exclamation marks pass through unchanged."""
    c = converter()
    result = c.convert("bahut achha!")
    assert "!" in result


def test_hindi_danda_preserved() -> None:
    """Hindi Devanagari danda (।) passes through unchanged."""
    c = converter()
    result = c.convert("namaste। kripaya suno।")
    assert "।" in result


def test_parentheses_preserved() -> None:
    """Parentheses pass through unchanged."""
    c = converter()
    result = c.convert("kist (EMI) ka bhugtan karo")
    assert "(" in result
    assert ")" in result


# ---------------------------------------------------------------------------
# Already-Devanagari passthrough
# ---------------------------------------------------------------------------


def test_pure_devanagari_passthrough() -> None:
    """Fully Devanagari text passes through without modification."""
    c = converter()
    text = "आपका बकाया राशि ₹50,000 है।"
    result = c.convert(text)
    assert result == text


def test_devanagari_unchanged_after_conversion() -> None:
    """Devanagari words in a mixed string are not double-processed."""
    c = converter()
    original_deva = "नमस्ते"
    result = c.convert(f"{original_deva} namaste")
    assert result.count("नमस्ते") == 2


# ---------------------------------------------------------------------------
# Unicode correctness
# ---------------------------------------------------------------------------


def test_output_is_valid_utf8() -> None:
    """Converter output encodes and decodes correctly as UTF-8."""
    c = converter()
    result = c.convert("namaste aapka bakaya 50000 hai")
    assert is_valid_unicode(result)


def test_devanagari_codepoints_in_nfc() -> None:
    """Devanagari output is in NFC normalisation form."""
    c = converter()
    result = c.convert("namaste")
    assert result == unicodedata.normalize("NFC", result)


def test_no_pua_delimiters_in_output() -> None:
    """Internal PUA placeholder delimiters are never exposed in output."""
    c = converter()
    result = c.convert("namaste aap ka bakaya ₹1,00,000 hai aaj")
    assert "" not in result
    assert "" not in result


def test_unicode_correctness_common_words() -> None:
    """Common converted words produce the expected Devanagari codepoints."""
    c = converter()
    cases: dict[str, str] = {
        "namaste": "नमस्ते",
        "haan": "हाँ",
        "nahi": "नहीं",
        "kya": "क्या",
        "aaj": "आज",
        "kal": "कल",
        "bakaya": "बकाया",
    }
    for roman, expected in cases.items():
        assert c.convert(roman) == expected, f"Failed for '{roman}'"


# ---------------------------------------------------------------------------
# Long responses
# ---------------------------------------------------------------------------


def test_long_mixed_response_converts_correctly() -> None:
    """A long mixed response converts Hindi words and preserves English content."""
    c = converter()
    text = (
        "namaste, main aapki help karna chahta hun. "
        "aapka bakaya amount ₹75,000 hai. "
        "kya aap abhi payment kar sakte hain? "
        "EMI schedule ke liye hume call karein 1800-123-4567. "
        "please visit https://pay.example.com for online payment. "
        "shukriya aapke time ke liye."
    )
    result = c.convert(text)

    assert "नमस्ते" in result
    assert "बकाया" in result
    assert "अभी" in result
    assert "शुक्रिया" in result
    assert "₹75,000" in result
    assert "EMI" in result
    assert "1800-123-4567" in result
    assert "https://pay.example.com" in result
    assert _DELIM_L not in result
    assert is_valid_unicode(result)


def test_long_english_response_unchanged() -> None:
    """A long English response is returned verbatim."""
    c = converter()
    text = (
        "We understand that you are facing financial difficulties. "
        "Our team is ready to help you with a flexible repayment plan. "
        "Please contact us at support@example.com or call 1800-000-0000. "
        "Visit our website at www.example.com for more options."
    )
    result = c.convert(text)
    assert result == text


# ---------------------------------------------------------------------------
# Streaming conversion (convert_stream)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_convert_stream_basic() -> None:
    """convert_stream converts Hindi words split across chunks."""
    c = converter()
    gen = c.convert_stream(_chunks("namaste ", "aap ", "ka ", "bakaya ", "hai"))
    result = await collect_stream(gen)
    assert "नमस्ते" in result
    assert "बकाया" in result
    assert "है" in result


@pytest.mark.asyncio
async def test_convert_stream_preserves_numbers() -> None:
    """convert_stream preserves numbers within streamed chunks."""
    c = converter()
    gen = c.convert_stream(_chunks("bakaya ", "50000 ", "rupaye ", "hai"))
    result = await collect_stream(gen)
    assert "बकाया" in result
    assert "50000" in result


@pytest.mark.asyncio
async def test_convert_stream_single_chunk() -> None:
    """convert_stream works with a single chunk."""
    c = converter()
    gen = c.convert_stream(_chunks("namaste aaj"))
    result = await collect_stream(gen)
    assert "नमस्ते" in result
    assert "आज" in result


@pytest.mark.asyncio
async def test_convert_stream_empty_chunks() -> None:
    """convert_stream handles empty stream without error."""
    c = converter()
    gen = c.convert_stream(_chunks())
    result = await collect_stream(gen)
    assert result == ""


@pytest.mark.asyncio
async def test_convert_stream_english_only_passthrough() -> None:
    """convert_stream passes through English-only content."""
    c = converter()
    gen = c.convert_stream(_chunks("Hello ", "there, ", "how are you?"))
    result = await collect_stream(gen)
    assert "Hello" in result
    assert "there" in result
    assert not has_devanagari(result)


@pytest.mark.asyncio
async def test_convert_stream_preserves_url_in_stream() -> None:
    """convert_stream preserves URLs within streamed text."""
    c = converter()
    gen = c.convert_stream(_chunks("visit ", "https://pay.example.com ", "karein"))
    result = await collect_stream(gen)
    assert "https://pay.example.com" in result


@pytest.mark.asyncio
async def test_convert_stream_word_boundary_buffering() -> None:
    """convert_stream correctly buffers partial words across chunk boundaries."""
    c = converter()
    # 'namaste' is split across chunks — 'namas' + 'te'
    gen = c.convert_stream(_chunks("namas", "te aaj"))
    result = await collect_stream(gen)
    # The split word 'namaste' should be buffered and correctly converted
    assert "नमस्ते" in result
    assert "आज" in result


@pytest.mark.asyncio
async def test_convert_stream_final_word_flushed() -> None:
    """convert_stream flushes the last word even without trailing whitespace."""
    c = converter()
    gen = c.convert_stream(_chunks("haan"))
    result = await collect_stream(gen)
    assert "हाँ" in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_string_returns_empty() -> None:
    """Empty input returns empty string."""
    c = converter()
    assert c.convert("") == ""


def test_whitespace_only_returns_unchanged() -> None:
    """Whitespace-only input returns the same whitespace."""
    c = converter()
    assert c.convert("   ") == "   "


def test_single_space_unchanged() -> None:
    """A single space passes through unchanged."""
    c = converter()
    assert c.convert(" ") == " "


def test_newline_preserved() -> None:
    """Newline characters pass through unchanged."""
    c = converter()
    result = c.convert("namaste\naaj ka din")
    assert "\n" in result
    assert "नमस्ते" in result


def test_tab_preserved() -> None:
    """Tab characters pass through unchanged."""
    c = converter()
    result = c.convert("bakaya\t50000")
    assert "\t" in result


def test_hyphenated_word_preserved() -> None:
    """Hyphenated tokens pass through unchanged (they're not in the word map)."""
    c = converter()
    result = c.convert("no-cost EMI")
    assert "no-cost" in result
    assert "EMI" in result


def test_mixed_case_word_converts() -> None:
    """Word lookup is case-insensitive: 'Namaste' → नमस्ते."""
    c = converter()
    result = c.convert("Namaste")
    assert result == "नमस्ते"


def test_mixed_case_haan_converts() -> None:
    """'HAAN' (all-caps version of haan) is preserved as ALL-CAPS."""
    c = converter()
    result = c.convert("HAAN")
    # 'HAAN' is ALL-CAPS (2+ chars) → preserved as-is
    assert result == "HAAN"


def test_only_punctuation_unchanged() -> None:
    """A string of only punctuation passes through unchanged."""
    c = converter()
    assert c.convert("!?,।") == "!?,।"


# ---------------------------------------------------------------------------
# Configuration — enabled=False
# ---------------------------------------------------------------------------


def test_converter_disabled_passthrough() -> None:
    """When enabled=False, all text passes through unchanged."""
    c = HindiScriptConverter(enabled=False)
    text = "namaste aapka bakaya hai"
    assert c.convert(text) == text


def test_converter_disabled_property() -> None:
    """The enabled property reflects the constructor argument."""
    c1 = HindiScriptConverter(enabled=True)
    c2 = HindiScriptConverter(enabled=False)
    assert c1.enabled is True
    assert c2.enabled is False


@pytest.mark.asyncio
async def test_convert_stream_disabled_passthrough() -> None:
    """convert_stream with enabled=False passes all chunks through unchanged."""
    c = HindiScriptConverter(enabled=False)
    gen = c.convert_stream(_chunks("namaste ", "aaj"))
    result = await collect_stream(gen)
    assert "namaste" in result
    assert "aaj" in result
    assert not has_devanagari(result)


# ---------------------------------------------------------------------------
# Configuration — extra_words
# ---------------------------------------------------------------------------


def test_extra_words_added_and_converted() -> None:
    """Custom extra_words are converted correctly."""
    c = HindiScriptConverter(extra_words={"dost": "दोस्त", "yaad": "याद"})
    result = c.convert("mera dost yaad aata hai")
    assert "दोस्त" in result
    assert "याद" in result


def test_extra_words_override_base_map() -> None:
    """extra_words can override base map entries."""
    c = HindiScriptConverter(extra_words={"namaste": "CUSTOM"})
    assert c.convert("namaste") == "CUSTOM"


def test_extra_words_case_insensitive() -> None:
    """extra_words keys are normalised to lowercase for case-insensitive matching."""
    c = HindiScriptConverter(extra_words={"DOST": "दोस्त"})
    assert c.convert("dost") == "दोस्त"
    assert c.convert("Dost") == "दोस्त"


# ---------------------------------------------------------------------------
# Configuration — extra_preserve (words that must not convert)
# ---------------------------------------------------------------------------


def test_word_not_in_map_is_preserved() -> None:
    """Words not in the word map are preserved regardless of extra_preserve."""
    c = HindiScriptConverter()
    # 'hello' is not in the map → must be preserved
    assert c.convert("hello namaste") == "hello नमस्ते"


# ---------------------------------------------------------------------------
# Stats helper
# ---------------------------------------------------------------------------


def test_conversion_stats_converted_count() -> None:
    """conversion_stats returns correct converted count for known words."""
    c = converter()
    stats = c.conversion_stats("namaste aap")
    assert stats["converted"] >= 1


def test_conversion_stats_preserved_count() -> None:
    """conversion_stats returns non-zero preserved count for unknown words."""
    c = converter()
    stats = c.conversion_stats("hello world")
    assert stats["preserved"] >= 2


def test_conversion_stats_empty() -> None:
    """conversion_stats returns zeros for empty input."""
    c = converter()
    stats = c.conversion_stats("")
    assert stats["converted"] == 0
    assert stats["preserved"] == 0


def test_conversion_stats_disabled() -> None:
    """conversion_stats returns zeros when converter is disabled."""
    c = HindiScriptConverter(enabled=False)
    stats = c.conversion_stats("namaste aaj")
    assert stats["converted"] == 0
    assert stats["preserved"] == 0


# ---------------------------------------------------------------------------
# word_map property
# ---------------------------------------------------------------------------


def test_word_map_returns_dict() -> None:
    """word_map property returns a dict."""
    c = converter()
    assert isinstance(c.word_map, dict)


def test_word_map_contains_namaste() -> None:
    """word_map contains the base entry for 'namaste'."""
    c = converter()
    assert "namaste" in c.word_map
    assert c.word_map["namaste"] == "नमस्ते"


def test_word_map_is_copy() -> None:
    """Mutating the returned word_map does not affect the converter."""
    c = converter()
    wm = c.word_map
    wm["test_key"] = "test_value"
    assert "test_key" not in c.word_map


# ---------------------------------------------------------------------------
# Promise-to-pay scenario (integration with Sprint-010 test deferred)
# ---------------------------------------------------------------------------


def test_promise_to_pay_sentence_converts() -> None:
    """A typical PTP response converts Hindi words correctly."""
    c = converter()
    result = c.convert("haan main kal payment kar dunga, theek hai?")
    assert "हाँ" in result
    assert "कल" in result
    assert "ठीक" in result
    assert is_valid_unicode(result)


def test_consent_grant_sentence_converts() -> None:
    """A consent grant response converts Hindi words correctly."""
    c = converter()
    result = c.convert("ji haan, aap call kar sakte hain")
    assert "जी हाँ" in result
    assert "आप" in result


# ---------------------------------------------------------------------------
# Date / time scenarios
# ---------------------------------------------------------------------------


def test_aaj_kal_sentence() -> None:
    """Time words aaj and kal convert correctly in context."""
    c = converter()
    result = c.convert("aaj nahi, kal karta hun")
    assert "आज" in result
    assert "कल" in result


def test_abhi_converts_in_sentence() -> None:
    """'abhi' converts correctly surrounded by other text."""
    c = converter()
    result = c.convert("main abhi payment kar sakta hun")
    assert "अभी" in result


# ---------------------------------------------------------------------------
# Regression: no mangling of partial-word boundaries in stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_no_double_conversion() -> None:
    """Words are converted exactly once even when chunks overlap boundaries."""
    c = converter()
    gen = c.convert_stream(_chunks("namas", "te ", "aapka ", "naam"))
    result = await collect_stream(gen)
    # namaste should appear once
    assert result.count("नमस्ते") == 1


@pytest.mark.asyncio
async def test_stream_full_sentence_equivalence() -> None:
    """Streaming and batch conversion produce the same output."""
    c = converter()
    sentence = "namaste aapka bakaya 50000 hai aaj theek hai"

    # Batch
    batch_result = c.convert(sentence)

    # Streaming (word by word)
    words = sentence.split(" ")
    chunks_list = [w + " " for w in words[:-1]] + [words[-1]]
    gen = c.convert_stream(iter_from_list(chunks_list))
    stream_result = await collect_stream(gen)

    assert batch_result == stream_result


async def iter_from_list(items: list[str]) -> AsyncIterator[str]:
    """Helper: async iterator from a plain list."""
    for item in items:
        yield item
