# Production Call Transcripts — Sprint-029 Phase 2

This directory holds real VoiceOS call transcripts exported for founder validation.

## Format

Each file is a JSON transcript in the `TranscriptCustomerContext` + `TranscriptTurn` schema
consumed by `tests/ai_eval/founder_validation_suite.py`.

**Required fields per transcript:**
- `call_id` — unique identifier from the real call
- `customer_context` — `outstanding_amount_inr`, `loan_id`, `due_date`, `calls_today_count`, `call_hour`, `minimum_settlement_pct`
- `turns[]` — list of `{turn_index, speaker, text, human_intent, negotiation_offer_inr}`
- `completed` — `true` if the call reached a natural conclusion
- `first_audio_ms` — first-audio latency from OTel traces (may be `null` if unavailable)

## `human_intent` Annotation (Required for IntentAccuracy)

`human_intent` on customer turns must be labeled by a human annotator before the harness
can compute IntentAccuracy. Valid labels match `src/engines/intent/intents.py`:

```
PAYMENT_INTENT | DISPUTE | HARDSHIP | CONSENT_GRANT | CONSENT_REVOKE |
CALLBACK_REQUEST | ESCALATION | CONFUSION | ABUSIVE | OFF_TOPIC
```

Leave `null` for agent turns (not evaluated) and any customer turns that don't have
a clean label — these will be excluded from the accuracy denominator.

## Generating Transcripts

```bash
# Export from audit_log + Redis
export POSTGRES_DSN="postgresql://voiceos:voiceos_pw@101.53.137.131/voiceos"
export REDIS_URL="redis://:0e539e25b3e5ea96e7434dad38c6029557a9fb3b92f0869e@101.53.137.131:6379/0"
export JAEGER_URL="http://101.53.137.131:16686"

python3 scripts/evaluate/export_call_transcript.py \
  --tenant-id <uuid> \
  --since 2026-07-12 \
  --limit 100 \
  --output evaluation/call-samples/production/
```

## Sprint-029 Gate

Phase 2 requires ≥ 50 transcripts. See Sprint-029.md for full gate criteria.
