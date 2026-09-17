"""Unit tests for EmpathyDirectiveComposer (Path-A Phase 6d)."""

from __future__ import annotations

from src.engines.empathy_directive.directive import EmpathyDirective, EmpathyState
from src.engines.empathy_directive.engine import EmpathyDirectiveComposer, EmpathyStateClassifier


class TestEmpathyStateClassifier:
    def test_neutral_on_empty_text(self) -> None:
        classifier = EmpathyStateClassifier()

        assert classifier.classify("") == EmpathyState.NEUTRAL

    def test_neutral_on_plain_utterance(self) -> None:
        classifier = EmpathyStateClassifier()

        assert classifier.classify("Haan theek hai, kar dunga payment.") == EmpathyState.NEUTRAL

    def test_classifies_hardship_illness_roman(self) -> None:
        classifier = EmpathyStateClassifier()

        assert classifier.classify("Mummy ki tabiyat kharab hai abhi") == EmpathyState.HARDSHIP_ILLNESS

    def test_classifies_hardship_illness_devanagari(self) -> None:
        classifier = EmpathyStateClassifier()

        assert classifier.classify("Ghar mein कोई बीमार है") == EmpathyState.HARDSHIP_ILLNESS

    def test_classifies_hardship_job_loss(self) -> None:
        classifier = EmpathyStateClassifier()

        assert classifier.classify("Meri job chali gayi pichhle mahine") == EmpathyState.HARDSHIP_JOB_LOSS

    def test_classifies_hardship_salary_delay(self) -> None:
        classifier = EmpathyStateClassifier()

        assert classifier.classify("Salary abhi tak nahi aayi is mahine") == EmpathyState.HARDSHIP_SALARY_DLY

    def test_classifies_hardship_family(self) -> None:
        classifier = EmpathyStateClassifier()

        assert classifier.classify("Ghar mein problem chal rahi hai") == EmpathyState.HARDSHIP_FAMILY

    def test_classifies_hardship_financial(self) -> None:
        classifier = EmpathyStateClassifier()

        assert classifier.classify("Abhi paise nahi hain mere paas") == EmpathyState.HARDSHIP_FINANCIAL

    def test_classifies_frustration(self) -> None:
        classifier = EmpathyStateClassifier()

        assert classifier.classify("Arre yaar kitni baar bolun") == EmpathyState.FRUSTRATION

    def test_classifies_anxiety(self) -> None:
        classifier = EmpathyStateClassifier()

        assert classifier.classify("Bahut tension mein hoon is baat ko lekar") == EmpathyState.ANXIETY

    def test_classifies_resignation(self) -> None:
        classifier = EmpathyStateClassifier()

        assert classifier.classify("Kya karun, majboor hoon") == EmpathyState.RESIGNATION

    def test_classifies_anger(self) -> None:
        classifier = EmpathyStateClassifier()

        assert classifier.classify("Chup baitho, phone rakh do") == EmpathyState.ANGER

    def test_classifies_gratitude(self) -> None:
        classifier = EmpathyStateClassifier()

        assert classifier.classify("Thank you itni madad karne ke liye") == EmpathyState.GRATITUDE

    def test_classifies_relief(self) -> None:
        classifier = EmpathyStateClassifier()

        assert classifier.classify("Achha theek, ho jaayega") == EmpathyState.RELIEF

    def test_word_boundary_prevents_false_positive_substring_match(self) -> None:
        """"chinta" (anxiety) must not match inside an unrelated longer word."""
        classifier = EmpathyStateClassifier()

        assert classifier.classify("Machinta company ka number hai") == EmpathyState.NEUTRAL

    def test_pattern_order_prefers_more_specific_hardship_first(self) -> None:
        """Illness patterns are checked before generic family patterns —
        an utterance naming both should resolve to the more specific bucket."""
        classifier = EmpathyStateClassifier()

        assert classifier.classify("Ghar mein bimar hai sab pareshan") == EmpathyState.HARDSHIP_ILLNESS


class TestEmpathyDirectiveComposer:
    def test_neutral_utterance_yields_neutral_directive(self) -> None:
        composer = EmpathyDirectiveComposer()

        directive = composer.compose("Haan theek hai", bucket="confirm_date")

        assert directive.state == EmpathyState.NEUTRAL
        assert directive.acknowledgment == ""
        assert directive.listening_break_ms == 0

    def test_hardship_financial_yields_acknowledgment_and_prosody_dip(self) -> None:
        composer = EmpathyDirectiveComposer()

        directive = composer.compose("Abhi paise nahi hain", bucket="part_payment")

        assert directive.state == EmpathyState.HARDSHIP_FINANCIAL
        assert directive.acknowledgment != ""
        assert directive.rate_scale_delta < 0
        assert directive.energy_scale_delta < 0
        assert directive.listening_break_ms > 0

    def test_farewell_bucket_forces_neutral_regardless_of_text(self) -> None:
        composer = EmpathyDirectiveComposer()

        directive = composer.compose("Meri job chali gayi", bucket="farewell")

        assert directive.state == EmpathyState.NEUTRAL
        assert directive.acknowledgment == ""

    def test_loan_denial_bucket_forces_neutral(self) -> None:
        composer = EmpathyDirectiveComposer()

        directive = composer.compose("Tabiyat kharab hai", bucket="loan_denial")

        assert directive.state == EmpathyState.NEUTRAL

    def test_allow_close_on_ack_always_true(self) -> None:
        composer = EmpathyDirectiveComposer()

        directive = composer.compose("Thank you", bucket="confirm_date")

        assert directive.allow_close_on_ack is True

    def test_apply_to_reply_prepends_acknowledgment(self) -> None:
        composer = EmpathyDirectiveComposer()
        directive = composer.compose("Abhi paise nahi hain", bucket="part_payment")

        out = composer.apply_to_reply("Kab tak kar paayenge sir?", directive)

        assert out.startswith(directive.acknowledgment.rstrip().rstrip("।").strip() or "")
        assert out.endswith("Kab tak kar paayenge sir?")

    def test_apply_to_reply_is_noop_on_neutral(self) -> None:
        composer = EmpathyDirectiveComposer()
        directive = composer.compose("Haan theek hai", bucket="confirm_date")

        out = composer.apply_to_reply("Kab tak kar paayenge sir?", directive)

        assert out == "Kab tak kar paayenge sir?"

    def test_apply_to_reply_noop_on_manually_constructed_empty_ack_directive(self) -> None:
        composer = EmpathyDirectiveComposer()
        directive = EmpathyDirective(state=EmpathyState.HARDSHIP_FINANCIAL, acknowledgment="")

        out = composer.apply_to_reply("Kab tak kar paayenge sir?", directive)

        assert out == "Kab tak kar paayenge sir?"
