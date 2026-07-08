"""RelationshipMemoryStore — Postgres-backed per-customer state store.

Stores and retrieves RelationshipMemory records from the relationship_memory
table in PostgreSQL. The Postgres connection is injected so tests can use
TestPostgres and production can use a pooled connection.

Interface:
  get(customer_id)              → RelationshipMemory
  update(customer_id, summary)  → None

Architecture: V2 Ch12 (Relationship Memory).
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from prometheus_client import Counter, Histogram

from .schema import CallSummary, PromiseRecord, RelationshipMemory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

_RM_OPS = Counter(
    "relationship_memory_operations_total",
    "Total relationship memory operations",
    ["operation"],
)

_RM_LATENCY = Histogram(
    "relationship_memory_operation_latency_seconds",
    "Relationship memory operation latency in seconds",
    buckets=[0.005, 0.010, 0.025, 0.050, 0.100, 0.200],
)

# ---------------------------------------------------------------------------
# DDL — applied at startup to ensure the table exists
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS relationship_memory (
    customer_id          TEXT PRIMARY KEY,
    total_calls          INTEGER NOT NULL DEFAULT 0,
    ptp_history          JSONB   NOT NULL DEFAULT '[]',
    sentiment_history    JSONB   NOT NULL DEFAULT '[]',
    best_contact_time    TEXT    NOT NULL DEFAULT '',
    preferred_language   TEXT    NOT NULL DEFAULT 'hi-IN',
    escalation_count     INTEGER NOT NULL DEFAULT 0,
    last_call_outcome    TEXT    NOT NULL DEFAULT '',
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


class RelationshipMemoryStore:
    """PostgreSQL-backed per-customer relationship memory store.

    Architecture: V2 Ch12.

    The conn object must expose: cursor(), commit() — matching both
    psycopg2 connections and test helpers.
    """

    _TABLE: ClassVar[str] = "relationship_memory"

    def __init__(self, conn: Any) -> None:
        """
        Args:
            conn: A psycopg2 connection (or compatible test object).
        """
        self._conn = conn
        self._ensure_table()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get(self, customer_id: str) -> RelationshipMemory:
        """Retrieve relationship memory for a customer.

        Returns a fresh empty RelationshipMemory if no record exists.

        Args:
            customer_id: Authoritative customer identifier.
        """
        sql = f"""
            SELECT total_calls, ptp_history, sentiment_history,
                   best_contact_time, preferred_language,
                   escalation_count, last_call_outcome
            FROM {self._TABLE}
            WHERE customer_id = %s
        """
        cur = self._conn.cursor()
        cur.execute(sql, (customer_id,))
        row = cur.fetchone()

        if row is None:
            logger.debug("Relationship memory miss", extra={"customer_id": customer_id})
            _RM_OPS.labels(operation="get_miss").inc()
            return RelationshipMemory(customer_id=customer_id)

        (
            total_calls,
            ptp_json,
            sentiment_json,
            best_contact_time,
            preferred_language,
            escalation_count,
            last_call_outcome,
        ) = row

        ptp_list = json.loads(ptp_json) if isinstance(ptp_json, str) else ptp_json or []
        sent_list = json.loads(sentiment_json) if isinstance(sentiment_json, str) else sentiment_json or []

        ptp_records = tuple(PromiseRecord.model_validate(p) for p in ptp_list)
        _RM_OPS.labels(operation="get_hit").inc()

        return RelationshipMemory(
            customer_id=customer_id,
            total_calls=total_calls,
            ptp_history=ptp_records,
            sentiment_history=tuple(sent_list),
            best_contact_time=best_contact_time or "",
            preferred_language=preferred_language or "hi-IN",
            escalation_count=escalation_count,
            last_call_outcome=last_call_outcome or "",
        )

    def update(self, customer_id: str, summary: CallSummary) -> None:
        """Update relationship memory after a completed call.

        Creates a new record if none exists (upsert). Appends call
        outcome to history fields; does not overwrite the full history.

        Args:
            customer_id: Authoritative customer identifier.
            summary: Summary of the completed call.
        """
        current = self.get(customer_id)

        new_total = current.total_calls + 1
        new_ptp: tuple[PromiseRecord, ...] = current.ptp_history
        if summary.ptp is not None:
            new_ptp = (*current.ptp_history, summary.ptp)

        new_sentiments = (*current.sentiment_history, summary.sentiment)
        new_escalation = current.escalation_count + (1 if summary.escalated else 0)
        best_contact = summary.contact_time_label or current.best_contact_time
        language = summary.language or current.preferred_language

        ptp_json = json.dumps([p.model_dump() for p in new_ptp])
        sent_json = json.dumps(list(new_sentiments))

        sql = f"""
            INSERT INTO {self._TABLE} (
                customer_id, total_calls, ptp_history, sentiment_history,
                best_contact_time, preferred_language, escalation_count,
                last_call_outcome, updated_at
            ) VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, NOW())
            ON CONFLICT (customer_id) DO UPDATE SET
                total_calls       = EXCLUDED.total_calls,
                ptp_history       = EXCLUDED.ptp_history,
                sentiment_history = EXCLUDED.sentiment_history,
                best_contact_time = EXCLUDED.best_contact_time,
                preferred_language = EXCLUDED.preferred_language,
                escalation_count  = EXCLUDED.escalation_count,
                last_call_outcome = EXCLUDED.last_call_outcome,
                updated_at        = NOW()
        """

        cur = self._conn.cursor()
        cur.execute(
            sql,
            (
                customer_id,
                new_total,
                ptp_json,
                sent_json,
                best_contact,
                language,
                new_escalation,
                summary.outcome,
            ),
        )
        self._conn.commit()

        _RM_OPS.labels(operation="update").inc()
        logger.debug(
            "Relationship memory updated",
            extra={"customer_id": customer_id, "outcome": summary.outcome},
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_table(self) -> None:
        """Create relationship_memory table if it does not exist."""
        cur = self._conn.cursor()
        cur.execute(_CREATE_TABLE_SQL)
        self._conn.commit()
