#!/usr/bin/env python3
"""Export real VoiceOS call artifacts to founder-validation-suite JSON format.

Reads dialogue events from the audit_log (Postgres) or OpenTelemetry/Jaeger
spans (via Jaeger HTTP API) for a completed call, then writes a harness-ready
JSON transcript to evaluation/call-samples/production/.

Output schema matches TranscriptCustomerContext + TranscriptTurn as defined in
tests/ai_eval/founder_validation_suite.py.

Usage:
    # Export a single call by call_id
    python3 scripts/evaluate/export_call_transcript.py \\
        --call-id CALL-20260712-001 \\
        --output evaluation/call-samples/production/

    # Export all calls from Postgres audit_log for a tenant
    python3 scripts/evaluate/export_call_transcript.py \\
        --tenant-id <uuid> \\
        --since 2026-07-01 \\
        --output evaluation/call-samples/production/

    # Batch export ≥50 calls for Sprint-029 Phase 2 gate
    python3 scripts/evaluate/export_call_transcript.py \\
        --tenant-id <uuid> \\
        --limit 100 \\
        --output evaluation/call-samples/production/

Requires:
    POSTGRES_DSN  — e.g. postgresql://voiceos:voiceos_pw@101.53.137.131/voiceos
    JAEGER_URL    — e.g. http://101.53.137.131:16686 (optional; for first_audio_ms)

Architecture: Sprint-029 Phase 2; DocSuite-10 (AI Evaluation Handbook).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("voiceos.evaluate.export")

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Jaeger span extraction — first_audio_ms
# ---------------------------------------------------------------------------

def _extract_first_audio_ms(call_id: str, jaeger_url: str) -> float | None:
    """Query Jaeger for the call's trace and extract first-audio latency.

    Returns milliseconds from call-start to first PlaybackScheduler chunk,
    or None if the trace is not found or the span is absent.
    """
    try:
        import urllib.request
        url = f"{jaeger_url}/api/traces?service=voiceos-conversation-engine&tags=%7B%22call_id%22%3A%22{call_id}%22%7D&limit=1"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        traces = data.get("data", [])
        if not traces:
            return None
        spans = traces[0].get("spans", [])
        # Walk spans looking for PlaybackScheduler.enqueue or first-audio-chunk
        call_start_us: int | None = None
        first_audio_us: int | None = None
        for span in spans:
            op = span.get("operationName", "")
            start_us: int = span["startTime"]
            if op == "ConversationEngine.process_turn" and call_start_us is None:
                call_start_us = start_us
            if op in ("PlaybackScheduler.enqueue_first_chunk", "AudioClause.first_chunk") and first_audio_us is None:
                first_audio_us = start_us
        if call_start_us is not None and first_audio_us is not None:
            return (first_audio_us - call_start_us) / 1000.0  # μs → ms
        return None
    except Exception as exc:
        logger.warning("Jaeger query failed for call %s: %s", call_id, exc)
        return None


# ---------------------------------------------------------------------------
# Audit log extraction — dialogue events
# ---------------------------------------------------------------------------

def _load_call_events(pg_dsn: str, call_id: str) -> list[dict]:
    """Load all audit_log rows for ``call_id`` (resource_id match)."""
    import psycopg2  # type: ignore[import-untyped]

    conn = psycopg2.connect(pg_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT actor_id, action, resource_type, resource_id,
                       outcome, event_payload, recorded_at
                  FROM audit_log
                 WHERE resource_id = %s
                    OR event_payload->>'call_id' = %s
                 ORDER BY seq ASC
                """,
                (call_id, call_id),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    events = []
    for actor_id, action, res_type, res_id, outcome, payload_raw, recorded_at in rows:
        payload = payload_raw if isinstance(payload_raw, dict) else json.loads(payload_raw or "{}")
        events.append(
            {
                "actor_id": actor_id,
                "action": action,
                "resource_type": res_type,
                "resource_id": res_id,
                "outcome": outcome,
                "payload": payload,
                "recorded_at": recorded_at.isoformat() if recorded_at else None,
            }
        )
    return events


def _load_call_ids_for_tenant(pg_dsn: str, tenant_id: str, since: str | None, limit: int) -> list[str]:
    """Return distinct call_ids from audit_log for a tenant."""
    import psycopg2  # type: ignore[import-untyped]

    conn = psycopg2.connect(pg_dsn)
    try:
        with conn.cursor() as cur:
            where = "tenant_id = %s AND event_payload->>'call_id' IS NOT NULL"
            params: list = [tenant_id]
            if since:
                where += " AND recorded_at >= %s"
                params.append(since)
            cur.execute(
                f"""
                SELECT DISTINCT event_payload->>'call_id' AS call_id
                  FROM audit_log
                 WHERE {where}
                 ORDER BY call_id
                 LIMIT %s
                """,
                [*params, limit],
            )
            return [row[0] for row in cur.fetchall() if row[0]]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Redis CustomerContext fetch
# ---------------------------------------------------------------------------

def _load_customer_context(redis_url: str, call_id: str) -> dict:
    """Load the CustomerContext snapshot for this call from Redis Working Memory.

    Falls back to an empty context on miss — annotator must fill manually.
    """
    try:
        import redis as redis_lib  # type: ignore[import-untyped]
        r = redis_lib.from_url(redis_url, decode_responses=True)
        raw = r.get(f"voiceos:working_memory:{call_id}:customer_context")
        if raw:
            return json.loads(raw)
    except Exception as exc:
        logger.warning("Redis CustomerContext fetch failed for %s: %s", call_id, exc)
    return {}


# ---------------------------------------------------------------------------
# Event → transcript turn assembly
# ---------------------------------------------------------------------------

def _assemble_turns(events: list[dict]) -> list[dict]:
    """Convert audit_log events into transcript turn dicts.

    Heuristic mapping:
    - action=conversation.turn.agent_response  → speaker=agent
    - action=conversation.turn.customer_input  → speaker=customer
    - payload.negotiation_offer_inr            → negotiation_offer_inr turn field

    NOTE: `human_intent` labels are NOT populated here — they must be added
    manually by a human annotator before the harness can score IntentAccuracy.
    """
    turns = []
    idx = 0
    for ev in events:
        action = ev["action"]
        payload = ev["payload"]
        text = payload.get("text") or payload.get("transcript") or payload.get("response_text") or ""
        if not text:
            continue
        if "agent_response" in action or "llm_output" in action:
            turns.append(
                {
                    "turn_index": idx,
                    "speaker": "agent",
                    "text": text,
                    "human_intent": None,
                    "negotiation_offer_inr": payload.get("negotiation_offer_inr"),
                }
            )
            idx += 1
        elif "customer_input" in action or "stt_output" in action:
            turns.append(
                {
                    "turn_index": idx,
                    "speaker": "customer",
                    "text": text,
                    "human_intent": None,  # must be labeled by human annotator
                    "negotiation_offer_inr": None,
                }
            )
            idx += 1
    return turns


def _build_customer_context(ctx: dict) -> dict:
    """Map real CustomerContext fields to the harness TranscriptCustomerContext schema."""
    outstanding = ctx.get("outstanding_amount_inr") or ctx.get("total_outstanding_inr") or 0.0
    return {
        "outstanding_amount_inr": float(outstanding),
        "outstanding_amount_minor": int(outstanding * 100),
        "minimum_settlement_pct": ctx.get("minimum_settlement_pct", 0.5),
        "loan_id": ctx.get("loan_id") or ctx.get("account_id") or "UNKNOWN",
        "due_date": ctx.get("due_date") or ctx.get("next_due_date") or "UNKNOWN",
        "calls_today_count": ctx.get("calls_today_count", 1),
        "call_hour": ctx.get("call_hour", 14),
    }


# ---------------------------------------------------------------------------
# Export a single call
# ---------------------------------------------------------------------------

def export_call(
    call_id: str,
    pg_dsn: str,
    redis_url: str,
    jaeger_url: str | None,
    output_dir: Path,
) -> Path:
    """Export one call to a harness JSON file. Returns the output path."""
    events = _load_call_events(pg_dsn, call_id)
    if not events:
        logger.warning("No audit events found for call_id=%s — skipping", call_id)
        raise ValueError(f"No events for call {call_id}")

    ctx_raw = _load_customer_context(redis_url, call_id)
    customer_context = _build_customer_context(ctx_raw)
    turns = _assemble_turns(events)

    first_audio_ms = None
    if jaeger_url:
        first_audio_ms = _extract_first_audio_ms(call_id, jaeger_url)

    completed = any(
        "call_completed" in ev["action"] or "call_ended" in ev["action"]
        for ev in events
    )

    transcript = {
        "_comment": (
            f"Exported {datetime.now(UTC).isoformat()} from audit_log. "
            "NOTE: human_intent fields require manual annotation before Phase-1 IntentAccuracy scoring."
        ),
        "call_id": call_id,
        "customer_context": customer_context,
        "turns": turns,
        "completed": completed,
        "first_audio_ms": first_audio_ms,
    }

    slug = call_id.replace("/", "_").replace(":", "_")
    output_path = output_dir / f"{slug}.json"
    output_path.write_text(json.dumps(transcript, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Exported %s → %s (%d turns)", call_id, output_path, len(turns))
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Export VoiceOS call transcripts for founder validation")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--call-id", help="Single call ID to export")
    group.add_argument("--tenant-id", help="Export all calls for this tenant")
    parser.add_argument("--output", default="evaluation/call-samples/production/", help="Output directory")
    parser.add_argument("--since", help="Filter calls after this date (YYYY-MM-DD), only with --tenant-id")
    parser.add_argument("--limit", type=int, default=100, help="Max calls to export, only with --tenant-id")
    args = parser.parse_args()

    pg_dsn = os.environ.get("POSTGRES_DSN", "postgresql://voiceos:voiceos_pw@101.53.137.131/voiceos")
    redis_url = os.environ.get("REDIS_URL", "redis://:0e539e25b3e5ea96e7434dad38c6029557a9fb3b92f0869e@101.53.137.131:6379/0")
    jaeger_url = os.environ.get("JAEGER_URL")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.call_id:
        call_ids = [args.call_id]
    else:
        call_ids = _load_call_ids_for_tenant(pg_dsn, args.tenant_id, args.since, args.limit)
        logger.info("Found %d call(s) for tenant %s", len(call_ids), args.tenant_id)

    exported = 0
    failed = 0
    for cid in call_ids:
        try:
            export_call(cid, pg_dsn, redis_url, jaeger_url, output_dir)
            exported += 1
        except Exception as exc:
            logger.error("Failed to export %s: %s", cid, exc)
            failed += 1

    logger.info("Done: %d exported, %d failed → %s", exported, failed, output_dir)
    if exported < 50:
        logger.warning(
            "Sprint-029 Phase 2 requires ≥50 production transcripts. "
            "Have %d. Run more calls or widen --since / --limit.",
            exported,
        )


if __name__ == "__main__":
    main()
