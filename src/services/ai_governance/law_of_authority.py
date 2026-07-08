"""LawOfAuthorityChecker — verifies no invented facts in LLM output (RI-5).

Extracts amounts, dates, and account numbers from the LLM's generated text
and verifies each against the sealed ``ResponsePlan.facts`` — the only
authoritative source of customer-facing facts (Law of Authority, CLAUDE.md).
Amounts require an exact match; dates are permitted a ±1 day tolerance for
natural phrasing ("tomorrow" vs. the exact due date). Any extracted fact
that cannot be grounded in ``ResponsePlan.facts`` is reported as a
violation — the caller (:class:`~.governance_layer.GovernanceLayer`) turns
this into a BLOCK verdict.

Architecture: V1 Appendix E RI-5; Law of Authority; V4 Ch3.
"""

from __future__ import annotations

import re
from datetime import datetime

from src.libs.contracts.response_plan import ResponsePlan
from src.libs.invariants.errors import InvariantViolationError
from src.libs.invariants.guards import assert_ri5_law_of_authority

AUTHORITATIVE_SOURCE = "response_plan.facts"
"""The only source ``assert_ri5_law_of_authority`` ever authorizes here."""

LLM_GENERATED_SOURCE = "llm_generated"
"""Attributed to any extracted fact that could not be grounded in ResponsePlan.facts."""

_AMOUNT_PATTERN = re.compile(r"(?:₹|Rs\.?\s*|INR\s*)(\d[\d,]*(?:\.\d{1,2})?)")
_ACCOUNT_NUMBER_PATTERN = re.compile(r"\b\d{8,}\b")
_ISO_DATE_PATTERN = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY_DATE_PATTERN = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")

_AMOUNT_TOLERANCE = 0.01
_DATE_TOLERANCE_DAYS = 1


class LawOfAuthorityChecker:
    """Scans free-form LLM output text for facts and grounds each in ``ResponsePlan.facts``."""

    def check(self, llm_output: str, response_plan: ResponsePlan) -> tuple[str, ...]:
        """Return violation descriptions for every ungrounded fact found.

        An empty tuple means every extracted amount/date/account number
        matched an authoritative fact (or none were present) — the output
        may proceed.
        """
        violations: list[str] = [
            *self._check_amounts(llm_output, response_plan),
            *self._check_account_numbers(llm_output, response_plan),
            *self._check_dates(llm_output, response_plan),
        ]
        return tuple(violations)

    # ------------------------------------------------------------------
    # Amounts — exact match required
    # ------------------------------------------------------------------

    def _check_amounts(self, text: str, response_plan: ResponsePlan) -> list[str]:
        authoritative = self._authoritative_amounts(response_plan)
        violations: list[str] = []
        for raw in _AMOUNT_PATTERN.findall(text):
            try:
                amount = round(float(raw.replace(",", "")), 2)
            except ValueError:
                continue
            matched = any(abs(amount - a) < _AMOUNT_TOLERANCE for a in authoritative)
            violations.extend(self._verify(fact_key=f"amount:{amount}", value=amount, matched=matched))
        return violations

    @staticmethod
    def _authoritative_amounts(response_plan: ResponsePlan) -> set[float]:
        amounts: set[float] = set()
        for key, value in response_plan.facts.items():
            if not isinstance(value, int | float) or isinstance(value, bool):
                continue
            amounts.add(round(value / 100, 2) if key.endswith("_minor") else round(float(value), 2))
        return amounts

    # ------------------------------------------------------------------
    # Account numbers — exact match required
    # ------------------------------------------------------------------

    def _check_account_numbers(self, text: str, response_plan: ResponsePlan) -> list[str]:
        authoritative = self._authoritative_account_numbers(response_plan)
        if not authoritative:
            # No account-like fact exists to ground against — skip rather
            # than flag every long digit run (phone numbers, OTPs) as a
            # fabricated account number with no authoritative context.
            return []
        violations: list[str] = []
        for candidate in _ACCOUNT_NUMBER_PATTERN.findall(text):
            matched = candidate in authoritative
            violations.extend(self._verify(fact_key=f"account_number:{candidate}", value=candidate, matched=matched))
        return violations

    @staticmethod
    def _authoritative_account_numbers(response_plan: ResponsePlan) -> set[str]:
        numbers: set[str] = set()
        for key, value in response_plan.facts.items():
            if "account" not in key.lower() or value is None:
                continue
            numbers.add(str(value))
        return numbers

    # ------------------------------------------------------------------
    # Dates — ±1 day tolerance
    # ------------------------------------------------------------------

    def _check_dates(self, text: str, response_plan: ResponsePlan) -> list[str]:
        authoritative = self._authoritative_dates(response_plan)
        if not authoritative:
            return []
        violations: list[str] = []
        for parsed in self._extract_dates(text):
            matched = any(abs((parsed - d).days) <= _DATE_TOLERANCE_DAYS for d in authoritative)
            violations.extend(self._verify(fact_key=f"date:{parsed.date()}", value=str(parsed.date()), matched=matched))
        return violations

    @staticmethod
    def _authoritative_dates(response_plan: ResponsePlan) -> list[datetime]:
        dates: list[datetime] = []
        for key, value in response_plan.facts.items():
            if "date" not in key.lower() or not isinstance(value, str):
                continue
            try:
                dates.append(datetime.fromisoformat(value))
            except ValueError:
                continue
        return dates

    @staticmethod
    def _extract_dates(text: str) -> list[datetime]:
        parsed: list[datetime] = []
        for match in _ISO_DATE_PATTERN.finditer(text):
            year, month, day = (int(g) for g in match.groups())
            try:
                parsed.append(datetime(year, month, day))
            except ValueError:
                continue
        for match in _DMY_DATE_PATTERN.finditer(text):
            day, month, year = (int(g) for g in match.groups())
            if year < 100:
                year += 2000
            try:
                parsed.append(datetime(year, month, day))
            except ValueError:
                continue
        return parsed

    # ------------------------------------------------------------------
    # Shared RI-5 invocation
    # ------------------------------------------------------------------

    @staticmethod
    def _verify(fact_key: str, value: object, matched: bool) -> list[str]:
        """Call ``assert_ri5_law_of_authority`` for one extracted fact.

        ``source`` is the fact's *grounding* — ``AUTHORITATIVE_SOURCE`` if
        it matches a value in ``ResponsePlan.facts``, else
        ``LLM_GENERATED_SOURCE`` (an invented value the LLM introduced on
        its own). The guard raises whenever ``source`` is not authorized,
        i.e. whenever the fact was not grounded.
        """
        try:
            assert_ri5_law_of_authority(
                fact_key=fact_key,
                value=value,
                authorized_sources={AUTHORITATIVE_SOURCE},
                source=AUTHORITATIVE_SOURCE if matched else LLM_GENERATED_SOURCE,
            )
        except InvariantViolationError as exc:
            return [str(exc)]
        return []
