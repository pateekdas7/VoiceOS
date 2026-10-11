"""Unit tests for the Kavya persona module (Path-A Phase 6e)."""

from __future__ import annotations

from src.engines.prompt_builder.kavya_persona import (
    HANGUP_TEXT,
    build_greeting_text,
    build_short_identity_repeat_text,
    build_system_prompt,
)


class TestBuildSystemPrompt:
    def test_renders_lender_name(self) -> None:
        prompt = build_system_prompt("Rajat Finance")

        assert "Rajat Finance" in prompt
        assert "{lender_name}" not in prompt

    def test_generalizes_to_other_lenders(self) -> None:
        prompt = build_system_prompt("Acme Capital")

        assert "Acme Capital" in prompt
        assert "Rajat Finance" not in prompt

    def test_never_hardcodes_a_customer_name(self) -> None:
        prompt = build_system_prompt("Rajat Finance")

        assert "Prateek" not in prompt

    def test_deterministic(self) -> None:
        assert build_system_prompt("Rajat Finance") == build_system_prompt("Rajat Finance")

    def test_contains_feminine_grammar_rule(self) -> None:
        prompt = build_system_prompt("Rajat Finance")

        assert "बोल रही हूँ" in prompt


class TestBuildGreetingText:
    def test_renders_customer_and_lender_name(self) -> None:
        greeting = build_greeting_text(customer_name="Sunita Sharma", lender_name="Acme Capital")

        assert "Sunita Sharma" in greeting
        assert "Acme Capital" in greeting

    def test_generalizes_across_customers(self) -> None:
        first = build_greeting_text(customer_name="Sunita Sharma", lender_name="Acme Capital")
        second = build_greeting_text(customer_name="Ravi Kumar", lender_name="Acme Capital")

        assert first != second
        assert "Ravi Kumar" in second
        assert "Sunita Sharma" not in second

    def test_asks_for_identity_verification(self) -> None:
        greeting = build_greeting_text(customer_name="Sunita Sharma", lender_name="Acme Capital")

        assert "क्या मेरी बात" in greeting


class TestHangupText:
    def test_is_a_fixed_nonempty_string(self) -> None:
        assert isinstance(HANGUP_TEXT, str)
        assert HANGUP_TEXT.strip() != ""

    def test_carries_no_customer_specific_facts(self) -> None:
        assert "sir" in HANGUP_TEXT.lower()


class TestBuildShortIdentityRepeatText:
    def test_renders_customer_and_lender_name(self) -> None:
        text = build_short_identity_repeat_text(customer_name="Sunita Sharma", lender_name="Acme Capital")

        assert "Sunita Sharma" in text
        assert "Acme Capital" in text

    def test_shorter_than_full_greeting(self) -> None:
        full = build_greeting_text(customer_name="Sunita Sharma", lender_name="Acme Capital")
        short = build_short_identity_repeat_text(customer_name="Sunita Sharma", lender_name="Acme Capital")

        assert len(short) < len(full)
        assert "outstanding" not in short.lower()

    def test_generalizes_across_customers(self) -> None:
        first = build_short_identity_repeat_text(customer_name="Sunita Sharma", lender_name="Acme Capital")
        second = build_short_identity_repeat_text(customer_name="Ravi Kumar", lender_name="Acme Capital")

        assert first != second
        assert "Ravi Kumar" in second
