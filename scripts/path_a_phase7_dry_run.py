#!/usr/bin/env python3
"""Path-A consolidation Phase 7 — full pipeline dry run against real infra.

Drives a multi-turn scripted-golden-path conversation through the REAL
composition root (deployment/cpu/app.py's build_conversation_engine()):
real GPU-backed TTS synthesis (Veena, via VeenaAdapter -> the restored GPU
node), real AIGovernanceService/OutputValidator gating, real Postgres for
PromiseToPayService persistence, and the real DialogueResponseEngine FSM
(Phase 6f) reading real ResponsePlanningEngine output (real IntentEngine/
EntityExtractor/NegotiationEngine, Phase 1/6a).

Not a live Twilio call (Call-002 is still pending explicit authorization)
and not driven through the WebSocket transport/mu-law audio layer — Phase
4's integration test already proves that layer's protocol correctness
against synthetic Twilio messages, and does not need real GPU backing to
do so. What this script proves that no existing test does: the actual
scripted reply text DialogueResponseEngine produces for a real multi-turn
exchange, synthesized as real audio bytes by the real GPU node, with a
real Postgres row if the conversation reaches a finalized commitment —
i.e. that Phase 6's wiring is not just unit-tested in isolation but
produces a coherent, audible, persisted call end to end.

Known gap (documented, not fixed here): the Qwen2.5-Omni founder-validation
evaluator (Sprint-029) that scored Call-001 is not a persistent service —
it was not running when this script was written, and standing it back up
is out of Phase 7's scope (validating the NEW Phase 6 wiring, not
re-running an unrelated evaluation stack). The WAV this script produces
can be fed to evaluate_trial.py by hand once/if that service is restarted.

Usage:
    POSTGRES_DSN=<dsn> REDIS_PASSWORD=<pw> GPU_NODE_HOST=<host> \
    TWILIO_ACCOUNT_SID=<sid> TWILIO_AUTH_TOKEN=<token> \
    python scripts/path_a_phase7_dry_run.py
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import sys
import uuid
import wave
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deployment", "cpu"))

from src.libs.contracts.context import (  # noqa: E402
    ConsentStatus,
    ContactInfo,
    CustomerContext,
    LoanSummary,
    OutstandingBalance,
    PartyInfo,
)
from src.libs.contracts.primitives import AccountId, Currency, CustomerId, Money, TenantId  # noqa: E402
from src.libs.contracts.turn import TurnInput, TurnRole  # noqa: E402
from src.services.playback.scheduler import PlaybackScheduler  # noqa: E402

TENANT_ID = str(uuid.uuid4())
CALL_ID = str(uuid.uuid4())
CUSTOMER_ID = str(uuid.uuid4())
LOAN_ACCOUNT_ID = f"acc-phase7-dryrun-{uuid.uuid4().hex[:8]}"
OUTSTANDING_MINOR = 5_000_000  # ₹50,000, matches Call-001's demo scenario


def _provision_fk_rows(conn: object) -> None:
    """Insert the customers/loan_accounts rows PromiseToPayService's FK
    constraints require, so a finalized commitment this run can actually
    persist instead of failing on a foreign-key violation for a customer
    that only exists in this script's in-memory CustomerContext. A real
    call's CustomerContext always comes from CustomerContextAssembler
    reading rows that already exist — this provisioning step exists only
    because this dry run's customer is synthetic, not because production
    code needs it."""
    now = datetime.utcnow()
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "INSERT INTO customers (customer_id, tenant_id, crm_id, name, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (CUSTOMER_ID, TENANT_ID, "crm-phase7-dryrun", "Anjali Verma", now, now),
        )
        cur.execute(
            "INSERT INTO loan_accounts (loan_account_id, tenant_id, customer_id, product_type, "
            "disbursed_amount_minor, interest_rate_bps, tenure_months, disbursement_date, "
            "maturity_date, dpd, outstanding_total_minor, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                LOAN_ACCOUNT_ID, TENANT_ID, CUSTOMER_ID, "PERSONAL_LOAN",
                OUTSTANDING_MINOR, 1500, 12, now.date(), now.date(), 45,
                OUTSTANDING_MINOR, now, now,
            ),
        )
    conn.commit()  # type: ignore[attr-defined]


def _cleanup_fk_rows(conn: object) -> None:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute("DELETE FROM promises_to_pay WHERE call_id = %s", (CALL_ID,))
        cur.execute("DELETE FROM loan_accounts WHERE loan_account_id = %s", (LOAN_ACCOUNT_ID,))
        cur.execute("DELETE FROM customers WHERE customer_id = %s", (CUSTOMER_ID,))
    conn.commit()  # type: ignore[attr-defined]


def _context() -> CustomerContext:
    loan = LoanSummary(
        account_id=AccountId(LOAN_ACCOUNT_ID),
        product_type="PERSONAL_LOAN",
        outstanding_balance=Money(amount_minor=OUTSTANDING_MINOR, currency=Currency.INR),
        dpd=45,
        total_overdue=Money(amount_minor=OUTSTANDING_MINOR, currency=Currency.INR),
    )
    return CustomerContext(
        customer_id=CustomerId(CUSTOMER_ID),
        tenant_id=TenantId(TENANT_ID),
        primary_party=PartyInfo(
            party_id=CustomerId(CUSTOMER_ID),
            role="BORROWER",
            name="Anjali Verma",
            contact=ContactInfo(phone_number="+919812345678"),  # type: ignore[arg-type]
        ),
        loans=(loan,),
        outstanding=OutstandingBalance(
            total_outstanding=Money(amount_minor=OUTSTANDING_MINOR, currency=Currency.INR),
            total_overdue=Money(amount_minor=OUTSTANDING_MINOR, currency=Currency.INR),
            account_count=1,
        ),
        consent_status=ConsentStatus.GRANTED,
    )


def _turn(transcript: str, turn_index: int) -> TurnInput:
    return TurnInput(
        turn_id=str(uuid.uuid4()),
        call_id=CALL_ID,
        tenant_id=TENANT_ID,
        role=TurnRole.CUSTOMER,
        transcript=transcript,
        segments=(),
        created_at=datetime.utcnow(),
        correlation_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        turn_index=turn_index,
    )


async def main() -> None:
    import app as composition_root  # deployment/cpu/app.py

    print("=" * 70)
    print(f"Path-A Phase 7 dry run — call_id={CALL_ID}")
    print("=" * 70)

    print("\n[1/4] Building the real ConversationEngine dependency graph...")
    engine = composition_root.build_conversation_engine()
    print("      OK — real Postgres/Redis/GPU-node-backed engine constructed.")

    fk_conn = composition_root.build_postgres_connection()
    _provision_fk_rows(fk_conn)
    print(f"      Provisioned synthetic customer/loan rows (customer_id={CUSTOMER_ID}, loan_account_id={LOAN_ACCOUNT_ID})")

    context = _context()
    playback = PlaybackScheduler()
    all_audio = bytearray()
    sample_rate = 24000

    print("\n[2/4] Speaking the call-open greeting (real GPU TTS)...")
    greeting_text = engine.build_greeting(context)
    assert greeting_text is not None, "build_greeting() returned None — dialogue_response not wired"
    print(f"      Kavya: {greeting_text}")
    clauses = await engine.speak_scripted_text(greeting_text, playback)
    assert clauses, "greeting produced zero audio clauses — TTS/validator/governance rejected it"
    for c in clauses:
        all_audio.extend(c.audio_data)
        sample_rate = c.sample_rate
    # Mirrors what CallOrchestrator._send_clauses() does in the real WS
    # entrypoint (Phase 7 found this was previously missing there too, see
    # the twilio_ws_entrypoint.py fix in this same commit series): drain
    # exactly what TrueStreamingPipeline enqueued into `playback` for this
    # turn's clauses, so the queue doesn't accumulate across turns and hit
    # RI-3's bounded-queue guard.
    for _ in clauses:
        playback.dequeue_nowait()
    print(f"      OK — {len(clauses)} audio clause(s), {sum(len(c.audio_data) for c in clauses)} bytes")

    script = [
        "haan speaking",
        "kitna outstanding hai mera",
        "abhi paise nahi hain, thoda tight hai",
        "5000 monthly de dunga",
        "haan pakka",
    ]

    try:
        print("\n[3/4] Running the scripted conversation...")
        for i, transcript in enumerate(script):
            print(f"\n  Turn {i}: Customer: {transcript}")
            turn = _turn(transcript, i)
            clauses = await engine.handle_turn(turn, playback, context=context)
            assert clauses, f"turn {i} produced zero audio clauses"
            # clause_index is actually a per-audio-*chunk* counter (every
            # ~85ms PCM chunk gets its own, monotonically increasing —
            # see VeenaAdapter._stream_clause), not a per-text-clause one;
            # every chunk belonging to the same text clause carries that
            # clause's full text. Dedupe by collapsing consecutive repeats
            # of the same text instead of by index.
            reply_parts: list[str] = []
            for c in clauses:
                if not reply_parts or reply_parts[-1] != c.text:
                    reply_parts.append(c.text)
            reply_text = " ".join(reply_parts)
            print(f"          Kavya: {reply_text}")
            for c in clauses:
                all_audio.extend(c.audio_data)
                sample_rate = c.sample_rate
            for _ in clauses:
                playback.dequeue_nowait()
            session = engine.get_session_state(CALL_ID)
            assert session is not None, f"no session state tracked after turn {i}"

        print("\n[4/4] Writing dry-run audio + checking Postgres for a persisted commitment...")
        # tempfile.gettempdir() rather than the repo tree: this script may run
        # as a different OS user than owns the repo checkout (e.g. `postgres`,
        # for Unix-socket peer auth), which lacks write permission there.
        out_path = os.path.join(tempfile.gettempdir(), f"{CALL_ID}.wav")
        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(bytes(all_audio))
        print(f"      OK — wrote {len(all_audio)} bytes ({len(all_audio) / (sample_rate * 2):.1f}s) to {out_path}")

        with fk_conn.cursor() as cur:
            cur.execute(
                "SELECT ptp_id, promised_amount_minor, promise_date FROM promises_to_pay WHERE call_id = %s",
                (CALL_ID,),
            )
            rows = cur.fetchall()
        if rows:
            for ptp_id, amount_minor, promise_date in rows:
                print(f"      OK — persisted PTP {ptp_id}: {amount_minor} minor units by {promise_date}")
        else:
            print("      No PTP persisted for this call (negotiation did not reach a finalized commitment this run —")
            print("      not necessarily a defect; NegotiationEngine's floor/ceiling decision is independent per turn).")

        print("\n" + "=" * 70)
        print("Path-A Phase 7 dry run PASSED — real GPU TTS + real Postgres + real")
        print("DialogueResponseEngine FSM, end to end, zero exceptions.")
        print("=" * 70)
    finally:
        _cleanup_fk_rows(fk_conn)
        fk_conn.close()
        print(f"\nCleaned up synthetic customer/loan/PTP rows for tenant_id={TENANT_ID}")


if __name__ == "__main__":
    asyncio.run(main())
