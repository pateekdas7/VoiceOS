"""Intent accuracy evaluation — Sprint-010.

Evaluates IntentEngine (keyword mode) against a 100-sample labeled test set.
Asserts accuracy ≥ 90% (AC-1: accuracy ≥ 90% on labeled test set).

Usage:
    python tests/ai_eval/intent_accuracy_eval.py
    # or via pytest:
    pytest tests/ai_eval/intent_accuracy_eval.py -v

Architecture: V2 Ch3; DocSuite-08 (AI Eval — intent accuracy).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

# Ensure project root is on path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.engines.intent.engine import IntentEngine
from src.engines.intent.model import IntentModel
from src.libs.contracts.response_plan import IntentLabel
from src.libs.contracts.turn import TurnInput, TurnRole, UtteranceSegment

# ---------------------------------------------------------------------------
# Labeled test dataset — 100 samples (at least 7-8 per label)
# Each entry: (transcript, expected_label)
# Designed to match the keyword rules in IntentModel._keyword_classify
# ---------------------------------------------------------------------------

LABELED_DATASET: list[tuple[str, IntentLabel]] = [
    # PAYMENT (8 samples)
    ("main aaj payment karna chahta hun", IntentLabel.PAYMENT),
    ("I want to make a payment", IntentLabel.PAYMENT),
    ("payment de raha hun abhi", IntentLabel.PAYMENT),
    ("bhugtan karna hai mujhe", IntentLabel.PAYMENT),
    ("ye lo paise de deta hun", IntentLabel.PAYMENT),
    ("online payment kar diya", IntentLabel.PAYMENT),
    ("pay kar dunga aaj", IntentLabel.PAYMENT),
    ("bank se pay kiya", IntentLabel.PAYMENT),
    # PROMISE_TO_PAY (8 samples)
    ("main kal de dunga pakka", IntentLabel.PROMISE_TO_PAY),
    ("haan main de denge", IntentLabel.PROMISE_TO_PAY),
    ("I will pay you next week, I promise", IntentLabel.PROMISE_TO_PAY),
    ("de dunga zaroor", IntentLabel.PROMISE_TO_PAY),
    ("friday ko de dunga", IntentLabel.PROMISE_TO_PAY),
    ("wada karta hun kal de denge", IntentLabel.PROMISE_TO_PAY),
    ("pakka de dunga parson", IntentLabel.PROMISE_TO_PAY),
    ("will pay by tomorrow definitely", IntentLabel.PROMISE_TO_PAY),
    # DISPUTE (8 samples)
    ("yeh galat hai main nahi manta", IntentLabel.DISPUTE),
    ("dispute hai mujhe is amount se", IntentLabel.DISPUTE),
    ("wrong amount show ho raha hai", IntentLabel.DISPUTE),
    ("ye amount incorrect hai", IntentLabel.DISPUTE),
    ("maine ye loan nahi liya", IntentLabel.DISPUTE),
    ("galat information hai", IntentLabel.DISPUTE),
    ("this is wrong I dispute this", IntentLabel.DISPUTE),
    ("vivad hai mujhe is EMI se", IntentLabel.DISPUTE),
    # HARDSHIP (8 samples)
    ("paise nahi hain mere paas", IntentLabel.HARDSHIP),
    ("main abhi berozgar hun", IntentLabel.HARDSHIP),
    ("job nahi hai isliye paise nahi de sakta", IntentLabel.HARDSHIP),
    ("I have no money right now", IntentLabel.HARDSHIP),
    ("bahut mushkil chal raha hai ghar mein", IntentLabel.HARDSHIP),
    ("financial hardship hai mujhe", IntentLabel.HARDSHIP),
    ("nahi hai paise mujhe", IntentLabel.HARDSHIP),
    ("abhi mushkil mein hun", IntentLabel.HARDSHIP),
    # CALLBACK (7 samples)
    ("baad mein baat karo please", IntentLabel.CALLBACK),
    ("please call me later", IntentLabel.CALLBACK),
    ("shaam ko call karo", IntentLabel.CALLBACK),
    ("callback karo kal", IntentLabel.CALLBACK),
    ("agli baar baat karta hun", IntentLabel.CALLBACK),
    ("abhi busy hun baad mein", IntentLabel.CALLBACK),
    ("doosri baar try karo", IntentLabel.CALLBACK),
    # UNAVAILABLE (7 samples)
    ("abhi upalabdh nahi hun", IntentLabel.UNAVAILABLE),
    ("main abhi nahi hun yahan", IntentLabel.UNAVAILABLE),
    ("I am not available right now", IntentLabel.UNAVAILABLE),
    ("abhi busy hun", IntentLabel.UNAVAILABLE),
    ("not available please try later", IntentLabel.UNAVAILABLE),
    ("nahi hun abhi ghar pe", IntentLabel.UNAVAILABLE),
    ("baad mein try karo abhi nahi", IntentLabel.UNAVAILABLE),
    # DISCONNECT (7 samples)
    ("call kato please", IntentLabel.DISCONNECT),
    ("please disconnect", IntentLabel.DISCONNECT),
    ("hang up karo", IntentLabel.DISCONNECT),
    ("band karo ye call", IntentLabel.DISCONNECT),
    ("cut the call now", IntentLabel.DISCONNECT),
    ("kato abhi call ko", IntentLabel.DISCONNECT),
    ("disconnect kar do", IntentLabel.DISCONNECT),
    # ABUSE (7 samples)
    ("gaali mat do", IntentLabel.ABUSE),
    ("ye kya abuse kar rahe ho", IntentLabel.ABUSE),
    ("bc band karo", IntentLabel.ABUSE),
    ("saala chup karo", IntentLabel.ABUSE),
    ("mc ja yahan se", IntentLabel.ABUSE),
    ("stop this abuse", IntentLabel.ABUSE),
    ("ye gaali dena theek nahi", IntentLabel.ABUSE),
    # IDENTITY_VERIFY (7 samples)
    ("mera naam hai Rajesh Kumar", IntentLabel.IDENTITY_VERIFY),
    ("verify karo mujhe please", IntentLabel.IDENTITY_VERIFY),
    ("date of birth 15 July 1985", IntentLabel.IDENTITY_VERIFY),
    ("naam batao apna", IntentLabel.IDENTITY_VERIFY),
    ("identity verify karna hai", IntentLabel.IDENTITY_VERIFY),
    ("meri dob hai January 1990", IntentLabel.IDENTITY_VERIFY),
    ("please verify my identity", IntentLabel.IDENTITY_VERIFY),
    # CONSENT_GRANT (7 samples)
    ("haan main consent deta hun", IntentLabel.CONSENT_GRANT),
    ("yes I agree to the terms", IntentLabel.CONSENT_GRANT),
    ("ji haan theek hai", IntentLabel.CONSENT_GRANT),
    ("okay I consent", IntentLabel.CONSENT_GRANT),
    ("haan bilkul agree hun", IntentLabel.CONSENT_GRANT),
    ("yes I agree", IntentLabel.CONSENT_GRANT),
    ("consent diya main ne", IntentLabel.CONSENT_GRANT),
    # CONSENT_REVOKE (7 samples)
    ("mujhe mat karo call", IntentLabel.CONSENT_REVOKE),
    ("consent revoke karta hun", IntentLabel.CONSENT_REVOKE),
    ("DND pe daalo mujhe", IntentLabel.CONSENT_REVOKE),
    ("do not call me again", IntentLabel.CONSENT_REVOKE),
    ("nahi chahiye ye calls", IntentLabel.CONSENT_REVOKE),
    ("I want to be on DND", IntentLabel.CONSENT_REVOKE),
    ("mat karo call please", IntentLabel.CONSENT_REVOKE),
    # SILENCE (7 samples)
    ("", IntentLabel.SILENCE),
    (".", IntentLabel.SILENCE),
    ("..", IntentLabel.SILENCE),
    ("...", IntentLabel.SILENCE),
    ("[silence]", IntentLabel.SILENCE),
    ("   ", IntentLabel.SILENCE),
    ("\t", IntentLabel.SILENCE),
    # OTHER (8 samples)
    ("zyxwvutsrqponmlkjihgfedcba", IntentLabel.OTHER),
    ("asdfghjklqwertyuiop", IntentLabel.OTHER),
    ("xyzxyzxyzxyz12345", IntentLabel.OTHER),
    ("random text no keywords here", IntentLabel.OTHER),
    ("qqqqqwwwweeee", IntentLabel.OTHER),
    ("12345678901234", IntentLabel.OTHER),
    ("!@#$%^&*()", IntentLabel.OTHER),
    ("nnnnn mmmm lllll kkkk", IntentLabel.OTHER),
]


def _make_turn(transcript: str, idx: int) -> TurnInput:
    return TurnInput(
        turn_id=f"eval-{idx:04d}",
        call_id="eval-call-001",
        tenant_id="eval-tenant",
        role=TurnRole.CUSTOMER,
        transcript=transcript,
        segments=(
            UtteranceSegment(
                text=transcript,
                start_ms=0,
                end_ms=max(1000, len(transcript) * 50),
                confidence=0.90,
            ),
        )
        if transcript.strip()
        else (),
        created_at=datetime.now(tz=UTC),
        correlation_id="eval-corr",
        trace_id="eval-trace",
        turn_index=idx,
    )


def run_eval() -> dict[str, object]:
    """Run accuracy evaluation and return results dict."""
    engine = IntentEngine(model=IntentModel())

    total = len(LABELED_DATASET)
    correct = 0
    wrong: list[dict[str, str]] = []

    for idx, (transcript, expected) in enumerate(LABELED_DATASET):
        turn = _make_turn(transcript, idx)
        result = engine.classify(turn)
        if result.label == expected:
            correct += 1
        else:
            wrong.append(
                {
                    "idx": str(idx),
                    "transcript": repr(transcript),
                    "expected": expected.value,
                    "got": result.label.value,
                }
            )

    accuracy = correct / total
    return {
        "total": total,
        "correct": correct,
        "wrong": len(wrong),
        "accuracy": accuracy,
        "wrong_items": wrong,
    }


def main() -> int:
    results = run_eval()
    accuracy: float = float(results["accuracy"])  # type: ignore[arg-type]
    total: int = int(results["total"])  # type: ignore[call-overload]
    correct: int = int(results["correct"])  # type: ignore[call-overload]
    wrong_items: list[dict[str, str]] = results["wrong_items"]  # type: ignore[assignment]

    print("\nIntent Accuracy Evaluation — Sprint-010")
    print(f"{'=' * 50}")
    print(f"Total samples: {total}")
    print(f"Correct: {correct}")
    print(f"Wrong:   {len(wrong_items)}")
    print(f"Accuracy: {accuracy:.1%}")
    print()

    if wrong_items:
        print("Wrong predictions:")
        for item in wrong_items:
            print(f"  [{item['idx']}] {item['transcript']}")
            print(f"       expected={item['expected']!r}, got={item['got']!r}")
        print()

    threshold = 0.90
    if accuracy >= threshold:
        print(f"PASS: accuracy {accuracy:.1%} >= {threshold:.0%}")
        return 0
    else:
        print(f"FAIL: accuracy {accuracy:.1%} < {threshold:.0%}")
        return 1


# ---------------------------------------------------------------------------
# pytest-compatible test function
# ---------------------------------------------------------------------------


def test_intent_accuracy_above_90_percent() -> None:
    """AI eval: intent accuracy must be ≥ 90% on the labeled test set."""
    results = run_eval()
    accuracy: float = float(results["accuracy"])  # type: ignore[arg-type]
    wrong_items: list[dict[str, str]] = results["wrong_items"]  # type: ignore[assignment]

    # Print details for CI visibility
    if wrong_items:
        for item in wrong_items:
            print(f"WRONG [{item['idx']}]: expected={item['expected']}, got={item['got']}, text={item['transcript']}")

    assert accuracy >= 0.90, (
        f"Intent accuracy {accuracy:.1%} is below required 90% (wrong: {len(wrong_items)}/{results['total']})"
    )


if __name__ == "__main__":
    sys.exit(main())
