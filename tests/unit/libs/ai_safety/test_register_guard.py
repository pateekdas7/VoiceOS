"""Unit tests for RegisterGuard (Path-A Phase 6c)."""

from __future__ import annotations

from src.libs.ai_safety.register_guard import (
    RegisterGuard,
    RegisterViolation,
    dedupe_name,
    sanitize_reply,
    strip_trailing_sir,
)


class TestRegisterGuardCheck:
    def test_clean_reply_passes(self) -> None:
        guard = RegisterGuard()

        result = guard.check("Sir, aapka payment kab tak ho jaayega?")

        assert result.clean is True
        assert result.violation is None

    def test_literary_word_flagged(self) -> None:
        # Fix C (Gate 3D): domain vocab (भुगतान, राशि, विवरण, कृपया,
        # धन्यवाद) was removed from _LITERARY because a collections agent
        # MUST be able to say those words. Genuinely literary Sanskrit
        # (प्रतीत, अवगत, वाक्य, अंतिम, अवशेष, रात्रि, समक्ष) is still
        # flagged. Test with one of the surviving entries.
        guard = RegisterGuard()

        result = guard.check("Yeh mujhe उचित प्रतीत ho raha hai.")

        assert result.clean is False
        assert result.violation == RegisterViolation.LITERARY

    def test_collections_domain_vocab_accepted(self) -> None:
        # Fix C (Gate 3D): a scripted/LLM reply containing collections-domain
        # vocab must NOT be rejected as literary — a collections agent needs
        # to be able to say भुगतान/राशि/विवरण/कृपया/धन्यवाद. This test locks
        # in the removal from _LITERARY so a future edit can not silently
        # re-add them.
        guard = RegisterGuard()
        for phrase in (
            "Kripya apna भुगतान jaldi kar dijiye.",
            "आपकी outstanding राशि kya hai sir?",
            "Account का विवरण mere paas hai.",
            "कृपया एक minute wait kariye.",
            "धन्यवाद sir, बात हुई.",
        ):
            r = guard.check(phrase)
            assert r.clean is True, (phrase, r.violation)

    def test_slang_flagged(self) -> None:
        guard = RegisterGuard()

        result = guard.check("Aap साला paisa nahi de rahe.")

        assert result.clean is False
        assert result.violation == RegisterViolation.SLANG

    def test_masculine_grammar_flagged(self) -> None:
        guard = RegisterGuard()

        result = guard.check("Main aapko call kar दूंगा kal.")

        assert result.clean is False
        assert result.violation == RegisterViolation.MASCULINE_GRAMMAR

    def test_masculine_grammar_flagged_in_roman_script(self) -> None:
        """Path-A Call-002 readiness: a real LLM was observed generating a
        masculine verb form in Roman script that a Devanagari-only phrase
        list never matched (scripts/path_a_llm_fallback_validation.py)."""
        guard = RegisterGuard()

        result = guard.check("Main is jaankari ki dobara pushti kar raha hoon.")

        assert result.clean is False
        assert result.violation == RegisterViolation.MASCULINE_GRAMMAR

    def test_masculine_suffix_rule_generalizes_to_an_unlisted_verb_roman(self) -> None:
        """The suffix regex (rule 1: <stem>a + hoon) must fire for a verb
        stem never enumerated anywhere — proving this is a systematic
        grammatical rule, not a phrase lookup table."""
        guard = RegisterGuard()

        result = guard.check("Main aapke liye naya loan account khareed raha hoon.")

        assert result.clean is False
        assert result.violation == RegisterViolation.MASCULINE_GRAMMAR

    def test_masculine_suffix_rule_generalizes_to_an_unlisted_verb_devanagari(self) -> None:
        guard = RegisterGuard()

        result = guard.check("Main aapka record खरीद रहा हूँ अभी।")

        assert result.clean is False
        assert result.violation == RegisterViolation.MASCULINE_GRAMMAR

    def test_masculine_future_suffix_rule_roman(self) -> None:
        """Rule 2 (<stem>unga) generalizes over verb stems too — "khareedunga"
        is not enumerated anywhere, matching only via the suffix pattern."""
        guard = RegisterGuard()

        result = guard.check("Main woh cheez khareedunga kal.")

        assert result.clean is False
        assert result.violation == RegisterViolation.MASCULINE_GRAMMAR

    def test_masculine_modal_sakta_generalizes_roman(self) -> None:
        guard = RegisterGuard()

        result = guard.check("Main yeh kaam khud kar sakta hoon.")

        assert result.clean is False
        assert result.violation == RegisterViolation.MASCULINE_GRAMMAR

    def test_feminine_continuous_form_is_clean(self) -> None:
        guard = RegisterGuard()

        result = guard.check("Main is jaankari ki dobara pushti kar rahi hoon.")

        assert result.clean is True

    def test_feminine_devanagari_continuous_form_is_clean(self) -> None:
        guard = RegisterGuard()

        result = guard.check("Main record खरीद रही हूँ अभी।")

        assert result.clean is True

    def test_feminine_future_form_is_clean(self) -> None:
        guard = RegisterGuard()

        result = guard.check("Main woh cheez khareedungi kal.")

        assert result.clean is True

    def test_feminine_modal_sakti_is_clean(self) -> None:
        guard = RegisterGuard()

        result = guard.check("Main yeh kaam khud kar sakti hoon.")

        assert result.clean is True

    def test_feminine_roman_grammar_is_clean(self) -> None:
        guard = RegisterGuard()

        result = guard.check("Main is jaankari ki dobara pushti kar rahi hoon.")

        assert result.clean is True

    def test_action_hallucination_flagged(self) -> None:
        guard = RegisterGuard()

        result = guard.check("Main बैंक जा kar check karti hoon.")

        assert result.clean is False
        assert result.violation == RegisterViolation.ACTION_HALLUCINATION

    def test_unsolicited_followup_flagged(self) -> None:
        guard = RegisterGuard()

        result = guard.check("Main aapko हर महीने कॉल karungi.")

        assert result.clean is False
        assert result.violation == RegisterViolation.UNSOLICITED_FOLLOWUP

    def test_hallucinated_recording_disclaimer_flagged(self) -> None:
        guard = RegisterGuard()

        result = guard.check("Yeh call recording ki ja rahi hai.")

        assert result.clean is False
        assert result.violation == RegisterViolation.HALLUCINATION

    def test_hallucinated_discount_offer_flagged(self) -> None:
        guard = RegisterGuard()

        result = guard.check("Hum aapko discount दे सकते hain.")

        assert result.clean is False
        assert result.violation == RegisterViolation.HALLUCINATION

    def test_foreign_script_flagged(self) -> None:
        guard = RegisterGuard()

        result = guard.check("こんにちは, sir aapka payment.")

        assert result.clean is False
        assert result.violation == RegisterViolation.FOREIGN_SCRIPT

    def test_foreign_script_checked_before_other_categories(self) -> None:
        guard = RegisterGuard()

        result = guard.check("साला こんにちは भुगतान")

        assert result.violation == RegisterViolation.FOREIGN_SCRIPT


class TestDedupeName:
    def test_strips_full_name_with_sir(self) -> None:
        out = dedupe_name("Sir Prateek Das sir, aapka payment due hai.", "Prateek Das")

        assert "Prateek" not in out
        assert "Das" not in out

    def test_strips_first_name_only_mention(self) -> None:
        out = dedupe_name("Prateek, aapka payment due hai.", "Prateek Das")

        assert "Prateek" not in out

    def test_generalizes_to_other_customer_names(self) -> None:
        out = dedupe_name("Sunita ji, aapka loan pending hai.", "Sunita Sharma")

        assert "Sunita" not in out

    def test_no_customer_name_is_noop(self) -> None:
        reply = "Sir, aapka payment due hai."

        assert dedupe_name(reply, "") == reply

    def test_unrelated_reply_unaffected(self) -> None:
        reply = "Sir, aapka payment due hai."

        out = dedupe_name(reply, "Prateek Das")

        assert out == reply


class TestSanitizeReply:
    def test_replaces_koi_nahi_fragment(self) -> None:
        out = sanitize_reply("Theek hai, कोई नहीं.")

        assert "कोई बात नहीं" in out

    def test_replaces_bare_aayi_hoon_fragment(self) -> None:
        out = sanitize_reply("Main aapki help ke liye आई हूँ")

        assert "बात कर रही हूँ" in out

    def test_no_match_is_noop(self) -> None:
        reply = "Sir, aapka payment due hai."

        assert sanitize_reply(reply) == reply


class TestStripTrailingSir:
    def test_strips_trailing_sir_with_period(self) -> None:
        out = strip_trailing_sir("Aapka payment due hai, sir.")

        assert not out.rstrip("।. ").lower().endswith("sir")

    def test_strips_bare_trailing_sir(self) -> None:
        out = strip_trailing_sir("Kab tak clear ho jaayega sir")

        assert not out.strip().lower().endswith("sir")

    def test_caps_sir_at_one_mention(self) -> None:
        out = strip_trailing_sir("Sir, aapka sir bahut zaroori hai sir.")

        assert out.lower().count("sir") == 1

    def test_leaves_single_inline_sir_untouched(self) -> None:
        out = strip_trailing_sir("Sir, aapka payment kab tak ho jaayega?")

        assert "Sir" in out
        assert out.lower().count("sir") == 1

    def test_empty_result_falls_back_to_original(self) -> None:
        out = strip_trailing_sir("sir")

        assert out == "sir"


class TestSystemFallbackConstantsAreRegisterCompliant:
    """Regression test for a real bug found via Path-A Call-002 readiness
    validation (scripts/path_a_llm_fallback_validation.py): RegisterGuard
    correctly rejected a real LLM-generated reply and TrueStreamingPipeline
    substituted the system-wide "safe" fallback text in its place — but
    that fallback constant was itself grammatically masculine (a
    pre-Kavya-persona Sprint-018 default), so the substitution silently
    reintroduced the exact class of violation it was meant to fix. Both
    fallback constants in the codebase must pass RegisterGuard.check()
    themselves, or a future edit to either could reintroduce this bug
    without any other test catching it."""

    def test_ai_governance_safe_fallback_response_is_clean(self) -> None:
        from src.services.ai_governance.verdict import SAFE_FALLBACK_RESPONSE

        result = RegisterGuard().check(SAFE_FALLBACK_RESPONSE)

        assert result.clean is True, f"SAFE_FALLBACK_RESPONSE violates {result.violation}"

    def test_output_validator_safe_fallback_is_clean(self) -> None:
        from src.services.llm_runtime.output_validator import _SAFE_FALLBACK

        result = RegisterGuard().check(_SAFE_FALLBACK)

        assert result.clean is True, f"OutputValidator's _SAFE_FALLBACK violates {result.violation}"
