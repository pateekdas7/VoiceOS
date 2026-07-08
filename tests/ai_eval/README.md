# AI Evaluation Tests

The AI evaluation layer is the top of the VoiceOS test pyramid. These tests
measure conversation quality, not correctness — they require real or
fine-tuned models and structured evaluation rubrics.

## What Is Evaluated

| Dimension            | Metric                        | Gate      | Implemented Sprint |
|----------------------|-------------------------------|-----------|-------------------|
| Intent accuracy      | Precision / recall / F1       | >90%      | Sprint-010        |
| Law of Authority     | Zero unauthorized fact claims | 100%      | Sprint-012        |
| Negotiation bounds   | Offer always in floor/ceiling | 100%      | Sprint-011        |
| Tone / empathy       | Rubric score                  | ≥3.5/5    | Sprint-029        |
| RBI compliance       | Zero violations               | 100%      | Sprint-017        |
| Audio quality        | MOS                           | ≥3.5      | Sprint-028        |
| First-audio latency  | p95 ≤ 1.5 s                   | Hard gate | Sprint-012        |

## Structure

```
tests/ai_eval/
├── README.md              — this file
├── test_intent_accuracy.py    — Sprint-010
├── test_law_of_authority.py   — Sprint-012
├── test_negotiation_bounds.py — Sprint-011
└── fixtures/
    ├── labelled_turns.json    — human-labelled ground truth
    └── evaluation_rubric.py   — founder validation scoring
```

## Running AI Evaluation Tests

AI eval tests require a running VoiceOS instance and a set of labelled
test conversations. They are NOT run in the standard CI pipeline — only
during Sprint-029 (Founder Validation) and on demand.

```bash
VOICEOS_ENDPOINT=http://localhost:8080 pytest tests/ai_eval/ -v --ai-eval
```

## Architecture Reference

V2 (all chapters — quality review); V6 Ch9 (Testing Standards — AI eval layer);
DocSuite-08 (Testing Catalog); DocSuite-10 (AI Evaluation Handbook).
