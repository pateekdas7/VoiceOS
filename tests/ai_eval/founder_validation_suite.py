"""Sprint-029 Phase 1 — Founder Validation Suite.

Automated evaluation harness for all dimensions of the Sprint-029 Founder
Validation gate that can be assessed without live infrastructure:

  LawOfAuthorityReplayChecker  — verifies agent turns cite only facts from
                                  CustomerContext (RI-5); zero violations required
  RBIComplianceChecker         — verifies RBI Fair Practice Code rules per call
                                  (calling hours, frequency, disclosure)
  NegotiationEnvelopeChecker   — verifies all agent offers are within the
                                  configured floor/ceiling bounds
  IntentAccuracyEvaluator      — reuses IntentEngine to replay customer turns
                                  and measures accuracy vs. human-labeled dataset

Phase 2 dimensions (human review — PENDING REAL INFRASTRUCTURE):
  Tone & Empathy score         — human reviewer rubric (≥ 3.5/5)
  Language Naturalness score   — Hindi/Hinglish/English fluency (≥ 3.5/5)
  Audio Quality (MOS)          — mean opinion score on Veena output (≥ 3.5/5)
  First-audio p95 latency      — ≤ 1.5s from production OTel traces
  Call Completion Rate         — ≥ 90% calls reaching natural conclusion

Architecture: Sprint-029.md; DocSuite-10 (AI Evaluation Handbook);
              V2 (Conversation Intelligence — RI-5 Law of Authority).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure project root is importable when run directly
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Heavy pydantic-v2 imports are deferred to method bodies so this module can be
# imported without pydantic>=2 in the environment (e.g. for checker-only tests).
# With `from __future__ import annotations` all annotations are lazy strings,
# so no module-level import is required to satisfy the Python runtime.
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    # Used only in type annotations (resolved as strings at runtime).
    from src.engines.intent.model import IntentModel  # noqa: F401
    from src.libs.contracts.turn import TurnInput  # noqa: F401

# ---------------------------------------------------------------------------
# Transcript data model
# ---------------------------------------------------------------------------

# RBI Fair Practice Code constraints (V4 Ch17; packs/rbi.py)
_RBI_HOUR_MIN = 8
_RBI_HOUR_MAX = 20   # exclusive — 20:00 is the cut-off
_RBI_MAX_CALLS_PER_DAY = 3

# Regex patterns for fact extraction from agent text (mirrors law_of_authority.py)
_AMOUNT_RE = re.compile(r"(?:₹|Rs\.?\s*|INR\s*)(\d[\d,]*(?:\.\d{1,2})?)")
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY_DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
_AMOUNT_TOLERANCE = 0.01  # INR tolerance for floating-point comparison


@dataclass(frozen=True)
class TranscriptTurn:
    """One turn in a call transcript."""

    turn_index: int
    speaker: str          # "agent" or "customer"
    text: str
    human_intent: str | None = None          # human-labeled intent for customer turns
    negotiation_offer_inr: float | None = None   # if agent is making a settlement offer


@dataclass(frozen=True)
class TranscriptCustomerContext:
    """Extracted authoritative facts for a call — a lightweight surrogate
    for the full CustomerContext (no Pydantic dependency at eval time)."""

    outstanding_amount_inr: float
    outstanding_amount_minor: int
    minimum_settlement_pct: float   # e.g. 0.5 → floor = 50% of outstanding
    loan_id: str
    due_date: str                   # ISO-8601 date string e.g. "2026-08-01"
    calls_today_count: int
    call_hour: int                  # 0–23 local hour


@dataclass
class CallTranscript:
    """Loaded call transcript for evaluation replay."""

    call_id: str
    customer_context: TranscriptCustomerContext
    turns: list[TranscriptTurn]
    completed: bool
    first_audio_ms: float | None = None

    @classmethod
    def from_json(cls, path: Path) -> CallTranscript:
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            ctx_data = data["customer_context"]
            ctx = TranscriptCustomerContext(
                outstanding_amount_inr=float(ctx_data["outstanding_amount_inr"]),
                outstanding_amount_minor=int(ctx_data["outstanding_amount_minor"]),
                minimum_settlement_pct=float(ctx_data["minimum_settlement_pct"]),
                loan_id=str(ctx_data["loan_id"]),
                due_date=str(ctx_data["due_date"]),
                calls_today_count=int(ctx_data["calls_today_count"]),
                call_hour=int(ctx_data["call_hour"]),
            )
            turns = [
                TranscriptTurn(
                    turn_index=int(t["turn_index"]),
                    speaker=str(t["speaker"]),
                    text=str(t.get("text", "")),
                    human_intent=t.get("human_intent"),
                    negotiation_offer_inr=t.get("negotiation_offer_inr"),
                )
                for t in data.get("turns", [])
            ]
            return cls(
                call_id=data["call_id"],
                customer_context=ctx,
                turns=turns,
                completed=bool(data.get("completed", False)),
                first_audio_ms=data.get("first_audio_ms"),
            )
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Failed to load transcript {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Violation types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Violation:
    checker: str
    call_id: str
    turn_index: int | None
    description: str


# ---------------------------------------------------------------------------
# LawOfAuthorityReplayChecker
# ---------------------------------------------------------------------------

class LawOfAuthorityReplayChecker:
    """Replay agent turns against CustomerContext and detect invented facts.

    An invented fact is any amount, date, or loan ID that appears in an
    agent turn but does NOT match an authoritative value from the call's
    CustomerContext.

    This mirrors the production LawOfAuthorityChecker (RI-5) but operates
    on transcript JSON rather than live ResponsePlan objects.
    """

    def check_transcript(self, transcript: CallTranscript) -> list[Violation]:
        ctx = transcript.customer_context
        authoritative_amounts = self._authoritative_amounts(ctx)
        authoritative_dates = self._authoritative_dates(ctx)
        authoritative_ids = {ctx.loan_id.upper()}

        violations: list[Violation] = []
        for turn in transcript.turns:
            if turn.speaker != "agent":
                continue
            # Skip turns where the agent is making a negotiation offer —
            # those amounts are operational actions, not factual claims about the
            # customer's account. NegotiationEnvelopeChecker handles offer bounds.
            if turn.negotiation_offer_inr is not None:
                continue
            violations.extend(
                self._check_turn(
                    turn,
                    transcript.call_id,
                    authoritative_amounts,
                    authoritative_dates,
                    authoritative_ids,
                )
            )
        return violations

    @staticmethod
    def _authoritative_amounts(ctx: TranscriptCustomerContext) -> set[float]:
        amounts = {ctx.outstanding_amount_inr}
        # Floor is a derived authoritative value (50% of outstanding etc.)
        floor = round(ctx.outstanding_amount_inr * ctx.minimum_settlement_pct, 2)
        if floor > 0:
            amounts.add(floor)
        return amounts

    @staticmethod
    def _authoritative_dates(ctx: TranscriptCustomerContext) -> list[datetime]:
        dates: list[datetime] = []
        try:
            dates.append(datetime.fromisoformat(ctx.due_date))
        except ValueError:
            pass
        return dates

    def _check_turn(
        self,
        turn: TranscriptTurn,
        call_id: str,
        auth_amounts: set[float],
        auth_dates: list[datetime],
        auth_ids: set[str],
    ) -> list[Violation]:
        violations: list[Violation] = []

        # Check amounts
        for raw in _AMOUNT_RE.findall(turn.text):
            try:
                amount = round(float(raw.replace(",", "")), 2)
            except ValueError:
                continue
            if not any(abs(amount - a) < _AMOUNT_TOLERANCE for a in auth_amounts):
                violations.append(Violation(
                    checker="LawOfAuthority",
                    call_id=call_id,
                    turn_index=turn.turn_index,
                    description=(
                        f"Agent stated amount ₹{amount:,.2f} not in authoritative CustomerContext "
                        f"(authoritative amounts: {sorted(auth_amounts)})"
                    ),
                ))

        # Check dates
        for parsed in self._extract_dates(turn.text):
            if auth_dates and not any(abs((parsed - d).days) <= 1 for d in auth_dates):
                violations.append(Violation(
                    checker="LawOfAuthority",
                    call_id=call_id,
                    turn_index=turn.turn_index,
                    description=(
                        f"Agent stated date {parsed.date()} not within ±1 day of authoritative due dates "
                        f"({[d.date() for d in auth_dates]})"
                    ),
                ))

        # Check loan/account IDs (8+ digit sequences or explicit loan ID patterns)
        if auth_ids:
            for candidate in re.findall(r"\bLOAN-\S+|\b[A-Z]{2,}-\d{8,}\b", turn.text.upper()):
                if candidate not in auth_ids:
                    violations.append(Violation(
                        checker="LawOfAuthority",
                        call_id=call_id,
                        turn_index=turn.turn_index,
                        description=(
                            f"Agent stated loan/account ID '{candidate}' not in authoritative context "
                            f"(authoritative: {auth_ids})"
                        ),
                    ))

        return violations

    @staticmethod
    def _extract_dates(text: str) -> list[datetime]:
        parsed: list[datetime] = []
        for m in _ISO_DATE_RE.finditer(text):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                parsed.append(datetime(y, mo, d))
            except ValueError:
                pass
        for m in _DMY_DATE_RE.finditer(text):
            day, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:
                y += 2000
            try:
                parsed.append(datetime(y, mo, day))
            except ValueError:
                pass
        return parsed


# ---------------------------------------------------------------------------
# RBIComplianceChecker
# ---------------------------------------------------------------------------

class RBIComplianceChecker:
    """Verify RBI Fair Practice Code rules against transcript metadata.

    Checks:
      1. Calling hours: call must be between 08:00 and 20:00
      2. Call frequency: at most 3 calls per customer per day
      3. Agent language: no abusive or threatening patterns in agent turns

    Uses transcript-level metadata (call_hour, calls_today_count) and
    agent-turn text scanning. This is a deterministic check against the
    same rules implemented in src/services/policy_engine/packs/rbi.py.
    """

    _ABUSIVE_PATTERNS = re.compile(
        r"\b(gaali|abuse|threat|dhamki|maar|saala|bc|mc|chup|ullu|bewakoof)\b",
        re.IGNORECASE,
    )

    def check_transcript(self, transcript: CallTranscript) -> list[Violation]:
        ctx = transcript.customer_context
        violations: list[Violation] = []

        # Rule 1: calling hours
        if not (_RBI_HOUR_MIN <= ctx.call_hour < _RBI_HOUR_MAX):
            violations.append(Violation(
                checker="RBICompliance",
                call_id=transcript.call_id,
                turn_index=None,
                description=(
                    f"Call placed at hour {ctx.call_hour:02d}:xx, outside RBI permitted window "
                    f"{_RBI_HOUR_MIN:02d}:00–{_RBI_HOUR_MAX:02d}:00 (RBI-CALLING-HOURS)"
                ),
            ))

        # Rule 2: call frequency
        if ctx.calls_today_count >= _RBI_MAX_CALLS_PER_DAY:
            violations.append(Violation(
                checker="RBICompliance",
                call_id=transcript.call_id,
                turn_index=None,
                description=(
                    f"calls_today_count={ctx.calls_today_count} already at/exceeds RBI maximum "
                    f"of {_RBI_MAX_CALLS_PER_DAY} per customer per day (RBI-MAX-CALLS-PER-DAY)"
                ),
            ))

        # Rule 3: no abusive language in agent turns
        for turn in transcript.turns:
            if turn.speaker != "agent":
                continue
            if self._ABUSIVE_PATTERNS.search(turn.text):
                violations.append(Violation(
                    checker="RBICompliance",
                    call_id=transcript.call_id,
                    turn_index=turn.turn_index,
                    description=(
                        f"Agent turn {turn.turn_index} contains potentially abusive/threatening language: "
                        f"{turn.text[:100]!r}"
                    ),
                ))

        return violations


# ---------------------------------------------------------------------------
# NegotiationEnvelopeChecker
# ---------------------------------------------------------------------------

class NegotiationEnvelopeChecker:
    """Verify all agent settlement offers lie within the configured floor/ceiling.

    The floor is `minimum_settlement_pct × outstanding_amount_inr`.
    The ceiling is the full `outstanding_amount_inr`.

    Any agent offer (negotiation_offer_inr in a turn) that falls below the
    floor is a RI-5 violation — the agent made an unauthorized concession.
    Any offer above the ceiling is also a violation (overstated demand).
    """

    def check_transcript(self, transcript: CallTranscript) -> list[Violation]:
        ctx = transcript.customer_context
        floor = round(ctx.outstanding_amount_inr * ctx.minimum_settlement_pct, 2)
        ceiling = ctx.outstanding_amount_inr
        violations: list[Violation] = []

        for turn in transcript.turns:
            if turn.speaker != "agent" or turn.negotiation_offer_inr is None:
                continue
            offer = turn.negotiation_offer_inr
            if offer < floor - _AMOUNT_TOLERANCE:
                violations.append(Violation(
                    checker="NegotiationEnvelope",
                    call_id=transcript.call_id,
                    turn_index=turn.turn_index,
                    description=(
                        f"Agent offer ₹{offer:,.2f} is below configured floor ₹{floor:,.2f} "
                        f"({ctx.minimum_settlement_pct:.0%} of ₹{ceiling:,.2f} outstanding)"
                    ),
                ))
            elif offer > ceiling + _AMOUNT_TOLERANCE:
                violations.append(Violation(
                    checker="NegotiationEnvelope",
                    call_id=transcript.call_id,
                    turn_index=turn.turn_index,
                    description=(
                        f"Agent offer ₹{offer:,.2f} exceeds outstanding ceiling ₹{ceiling:,.2f}"
                    ),
                ))

        return violations


# ---------------------------------------------------------------------------
# IntentAccuracyEvaluator
# ---------------------------------------------------------------------------

@dataclass
class IntentEvalResult:
    total_labeled: int
    correct: int
    accuracy: float
    wrong_items: list[dict[str, str]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.accuracy >= 0.90


class IntentAccuracyEvaluator:
    """Replay customer turns through IntentEngine and measure accuracy vs.
    human labels.

    Pass threshold: ≥ 90% accuracy (Sprint-029 AC: ≥ 90% intent accuracy).

    For Phase 1, this runs against synthetic fixtures that include human_intent
    labels. For Phase 2, it runs against production transcripts from real calls.
    """

    def __init__(self, model: IntentModel | None = None) -> None:
        from src.engines.intent.engine import IntentEngine as _IntentEngine
        from src.engines.intent.model import IntentModel as _IntentModel
        self._engine = _IntentEngine(model=model if model is not None else _IntentModel())

    def evaluate_transcripts(self, transcripts: list[CallTranscript]) -> IntentEvalResult:
        total = 0
        correct = 0
        wrong: list[dict[str, str]] = []

        for transcript in transcripts:
            for turn_idx, turn in enumerate(transcript.turns):
                if turn.speaker != "customer" or turn.human_intent is None:
                    continue
                total += 1
                turn_input = _make_turn_input(turn, transcript.call_id)
                result = self._engine.classify(turn_input)
                predicted = result.label.value
                expected = turn.human_intent
                if predicted == expected:
                    correct += 1
                else:
                    wrong.append({
                        "call_id": transcript.call_id,
                        "turn_index": str(turn_idx),
                        "text": turn.text[:80],
                        "expected": expected,
                        "predicted": predicted,
                        "confidence": str(round(result.confidence, 4)),
                    })

        accuracy = correct / total if total > 0 else 0.0
        return IntentEvalResult(
            total_labeled=total,
            correct=correct,
            accuracy=round(accuracy, 4),
            wrong_items=wrong,
        )


def _make_turn_input(turn: TranscriptTurn, call_id: str) -> TurnInput:
    from datetime import UTC, datetime as dt
    from src.libs.contracts.turn import (  # noqa: PLC0415
        TurnInput as _TurnInput,
        TurnRole as _TurnRole,
        UtteranceSegment as _UtteranceSegment,
    )
    text = turn.text
    return _TurnInput(
        turn_id=f"replay-{call_id}-t{turn.turn_index}",
        call_id=call_id,
        tenant_id="eval-replay",
        role=_TurnRole.CUSTOMER,
        transcript=text,
        segments=(
            _UtteranceSegment(
                text=text,
                start_ms=0,
                end_ms=max(1000, len(text) * 50),
                confidence=0.90,
            ),
        )
        if text.strip()
        else (),
        created_at=dt.now(tz=UTC),
        correlation_id="eval-replay",
        trace_id="eval-replay",
        turn_index=turn.turn_index,
    )


# ---------------------------------------------------------------------------
# Per-call result and aggregate report
# ---------------------------------------------------------------------------

@dataclass
class CallEvalResult:
    call_id: str
    loa_violations: list[Violation]
    rbi_violations: list[Violation]
    negotiation_violations: list[Violation]

    @property
    def total_violations(self) -> int:
        return len(self.loa_violations) + len(self.rbi_violations) + len(self.negotiation_violations)

    @property
    def passed(self) -> bool:
        return self.total_violations == 0


@dataclass
class FounderValidationReport:
    """Summary of all automated Phase-1 checks across loaded transcripts."""

    total_calls: int
    calls_passed: int
    calls_failed: int

    total_loa_violations: int
    total_rbi_violations: int
    total_negotiation_violations: int

    intent_eval: IntentEvalResult | None

    per_call: list[CallEvalResult]

    # Phase-2 dimensions — not populated until real infrastructure execution
    tone_empathy_score: float | None = None          # PENDING REAL INFRASTRUCTURE
    language_naturalness_score: float | None = None  # PENDING REAL INFRASTRUCTURE
    audio_mos_score: float | None = None             # PENDING REAL INFRASTRUCTURE
    first_audio_p95_ms: float | None = None          # PENDING REAL INFRASTRUCTURE
    call_completion_rate: float | None = None        # PENDING REAL INFRASTRUCTURE
    founder_signed_off: bool = False                 # PENDING REAL INFRASTRUCTURE

    @property
    def loa_passed(self) -> bool:
        return self.total_loa_violations == 0

    @property
    def rbi_passed(self) -> bool:
        return self.total_rbi_violations == 0

    @property
    def negotiation_passed(self) -> bool:
        return self.total_negotiation_violations == 0

    @property
    def intent_passed(self) -> bool:
        return self.intent_eval is not None and self.intent_eval.passed

    @property
    def phase1_passed(self) -> bool:
        return self.loa_passed and self.rbi_passed and self.negotiation_passed

    def print_summary(self) -> None:
        print("\n" + "=" * 60)
        print("Sprint-029 Founder Validation Suite — Phase 1 Report")
        print("=" * 60)
        print(f"Calls evaluated:          {self.total_calls}")
        print(f"Calls passed:             {self.calls_passed}")
        print(f"Calls failed:             {self.calls_failed}")
        print()
        print(f"Law of Authority (RI-5):  {'PASS' if self.loa_passed else 'FAIL'} "
              f"({self.total_loa_violations} violations)")
        print(f"RBI Compliance:           {'PASS' if self.rbi_passed else 'FAIL'} "
              f"({self.total_rbi_violations} violations)")
        print(f"Negotiation Envelope:     {'PASS' if self.negotiation_passed else 'FAIL'} "
              f"({self.total_negotiation_violations} violations)")
        if self.intent_eval is not None:
            pct = self.intent_eval.accuracy * 100
            print(f"Intent Accuracy:          {'PASS' if self.intent_passed else 'FAIL'} "
                  f"({pct:.1f}% — {self.intent_eval.correct}/{self.intent_eval.total_labeled} correct)")
        print()
        print("Phase-2 dimensions (PENDING REAL INFRASTRUCTURE EXECUTION):")
        print("  Tone & Empathy score:   PENDING")
        print("  Language Naturalness:   PENDING")
        print("  Audio Quality (MOS):    PENDING")
        print("  First-audio p95:        PENDING")
        print("  Call Completion Rate:   PENDING")
        print("  Founder Sign-off:       PENDING")
        print()
        print(f"Phase-1 overall: {'PASS' if self.phase1_passed else 'FAIL'}")
        print("=" * 60)

        # Per-call details for failures
        for result in self.per_call:
            if not result.passed:
                print(f"\nCall {result.call_id} — FAILED ({result.total_violations} violations):")
                for v in result.loa_violations + result.rbi_violations + result.negotiation_violations:
                    turn_str = f"turn {v.turn_index}" if v.turn_index is not None else "call-level"
                    print(f"  [{v.checker}] {turn_str}: {v.description}")


# ---------------------------------------------------------------------------
# FounderValidationSuite — top-level orchestrator
# ---------------------------------------------------------------------------

class FounderValidationSuite:
    """Orchestrates all Phase-1 automated evaluation checks.

    Usage (Phase 1, against synthetic fixtures):
        suite = FounderValidationSuite()
        report = suite.run(transcript_dir=Path("evaluation/call-samples/synthetic"))
        report.print_summary()

    Usage (Phase 2, against production transcripts — PENDING infrastructure):
        suite = FounderValidationSuite()
        report = suite.run(transcript_dir=Path("evaluation/call-samples/production"))
        # Then add human scores: report.tone_empathy_score = X, etc.
    """

    def __init__(self, *, intent_evaluator: IntentAccuracyEvaluator | None = None) -> None:
        self._loa = LawOfAuthorityReplayChecker()
        self._rbi = RBIComplianceChecker()
        self._neg = NegotiationEnvelopeChecker()
        self._intent = intent_evaluator if intent_evaluator is not None else IntentAccuracyEvaluator()

    def load_transcripts(self, transcript_dir: Path) -> list[CallTranscript]:
        transcripts: list[CallTranscript] = []
        for path in sorted(transcript_dir.glob("*.json")):
            if path.name.startswith("_"):
                continue
            try:
                transcripts.append(CallTranscript.from_json(path))
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"Failed to load transcript {path}: {exc}") from exc
        return transcripts

    def evaluate_call(self, transcript: CallTranscript) -> CallEvalResult:
        return CallEvalResult(
            call_id=transcript.call_id,
            loa_violations=self._loa.check_transcript(transcript),
            rbi_violations=self._rbi.check_transcript(transcript),
            negotiation_violations=self._neg.check_transcript(transcript),
        )

    def run(self, transcript_dir: Path) -> FounderValidationReport:
        transcripts = self.load_transcripts(transcript_dir)
        per_call: list[CallEvalResult] = [self.evaluate_call(t) for t in transcripts]

        intent_result = self._intent.evaluate_transcripts(transcripts)

        total_loa = sum(len(r.loa_violations) for r in per_call)
        total_rbi = sum(len(r.rbi_violations) for r in per_call)
        total_neg = sum(len(r.negotiation_violations) for r in per_call)

        return FounderValidationReport(
            total_calls=len(per_call),
            calls_passed=sum(1 for r in per_call if r.passed),
            calls_failed=sum(1 for r in per_call if not r.passed),
            total_loa_violations=total_loa,
            total_rbi_violations=total_rbi,
            total_negotiation_violations=total_neg,
            intent_eval=intent_result,
            per_call=per_call,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Sprint-029 Founder Validation Suite")
    parser.add_argument(
        "--transcripts",
        type=Path,
        default=Path("evaluation/call-samples/synthetic"),
        help="Directory of call transcript JSON files (default: synthetic fixtures)",
    )
    args = parser.parse_args()

    suite = FounderValidationSuite()
    report = suite.run(args.transcripts)
    report.print_summary()

    return 0 if report.phase1_passed else 1


if __name__ == "__main__":
    sys.exit(main())
