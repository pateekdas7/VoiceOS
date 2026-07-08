"""BIRepository — the ``bi_facts`` dedicated-schema warehouse (Sprint-024, V5 Ch21).

Deliberately does not subclass ``BaseRepository``: every helper on
``BaseRepository`` assumes the ``public`` schema's ``tenant_id`` column is
the tenant-isolation boundary (AR-8), but ``bi_facts.fact_daily`` is
intentionally keyed by an anonymized ``tenant_surrogate_key`` instead — the
whole point of the dedicated BI schema is that queries never filter/join on
a raw ``tenant_id`` (see :class:`~src.libs.contracts.models.bi.BIDimTenant`).

Architecture: V5 Ch21 (Business Intelligence Platform).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from ..contracts.models.bi import BIFactDaily
from ..contracts.primitives import TenantId

_FACT_DAILY_COLUMNS = (
    "fact_daily_id",
    "tenant_surrogate_key",
    "day",
    "revenue_minor",
    "usage_call_minutes",
    "usage_stt_tokens",
    "usage_llm_tokens",
    "usage_gpu_seconds",
    "ptp_rate",
    "recovery_rate",
    "compliance_score",
    "refreshed_at",
)


class BIRepository:
    """Queries against the ``bi_facts`` schema (dim_tenant + fact_daily)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def _cursor(self) -> Any:
        return self._conn.cursor()

    def _commit(self) -> None:
        self._conn.commit()

    def get_or_create_surrogate_key(self, tenant_id: TenantId) -> str:
        """Return the tenant's surrogate key, creating the dim_tenant row on first use."""
        cur = self._cursor()
        cur.execute("SELECT tenant_surrogate_key FROM bi_facts.dim_tenant WHERE tenant_id = %s", (tenant_id,))
        row = cur.fetchone()
        if row is not None:
            return str(row[0])

        surrogate_key = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO bi_facts.dim_tenant (tenant_surrogate_key, tenant_id, created_at) VALUES (%s, %s, NOW())",
            (surrogate_key, tenant_id),
        )
        self._commit()
        return surrogate_key

    def upsert_fact_daily(self, fact: BIFactDaily) -> BIFactDaily:
        """Insert or replace one tenant-day's fact row (BIWarehouse.refresh() is re-runnable)."""
        cur = self._cursor()
        cur.execute(
            """
            INSERT INTO bi_facts.fact_daily (
                fact_daily_id, tenant_surrogate_key, day, revenue_minor, usage_call_minutes,
                usage_stt_tokens, usage_llm_tokens, usage_gpu_seconds, ptp_rate, recovery_rate,
                compliance_score, refreshed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_surrogate_key, day)
            DO UPDATE SET
                revenue_minor = EXCLUDED.revenue_minor,
                usage_call_minutes = EXCLUDED.usage_call_minutes,
                usage_stt_tokens = EXCLUDED.usage_stt_tokens,
                usage_llm_tokens = EXCLUDED.usage_llm_tokens,
                usage_gpu_seconds = EXCLUDED.usage_gpu_seconds,
                ptp_rate = EXCLUDED.ptp_rate,
                recovery_rate = EXCLUDED.recovery_rate,
                compliance_score = EXCLUDED.compliance_score,
                refreshed_at = EXCLUDED.refreshed_at
            """,
            (
                fact.fact_daily_id,
                fact.tenant_surrogate_key,
                fact.day,
                fact.revenue_minor,
                fact.usage_call_minutes,
                fact.usage_stt_tokens,
                fact.usage_llm_tokens,
                fact.usage_gpu_seconds,
                fact.ptp_rate,
                fact.recovery_rate,
                fact.compliance_score,
                fact.refreshed_at,
            ),
        )
        self._commit()
        return fact

    def find_fact_for_tenant(self, tenant_id: TenantId, day: date) -> BIFactDaily | None:
        """Convenience lookup: resolve ``tenant_id`` -> surrogate key -> that day's fact row.

        Used only by first-party callers that already know ``tenant_id`` (e.g.
        ``ExecutiveDashboard``); ``CrossTenantBenchmarking`` never calls this —
        it queries ``find_all_facts_for_day`` and works only with surrogate keys.
        """
        cur = self._cursor()
        cur.execute("SELECT tenant_surrogate_key FROM bi_facts.dim_tenant WHERE tenant_id = %s", (tenant_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return self._find_fact(str(row[0]), day)

    def _find_fact(self, surrogate_key: str, day: date) -> BIFactDaily | None:
        cur = self._cursor()
        cols = ", ".join(_FACT_DAILY_COLUMNS)
        cur.execute(
            f"SELECT {cols} FROM bi_facts.fact_daily WHERE tenant_surrogate_key = %s AND day = %s",
            (surrogate_key, day),
        )
        row = cur.fetchone()
        return self._hydrate_fact(row) if row is not None else None

    def find_all_facts_for_day(self, day: date) -> tuple[BIFactDaily, ...]:
        """All tenants' fact rows for ``day``, keyed only by surrogate key —
        the anonymized read path ``CrossTenantBenchmarking`` uses.
        """
        cur = self._cursor()
        cols = ", ".join(_FACT_DAILY_COLUMNS)
        cur.execute(f"SELECT {cols} FROM bi_facts.fact_daily WHERE day = %s", (day,))
        return tuple(self._hydrate_fact(row) for row in cur.fetchall())

    def find_facts_for_surrogate(self, surrogate_key: str, start: date, end: date) -> tuple[BIFactDaily, ...]:
        """A single tenant's fact history (by surrogate key) — used by ``ForecastingEngine``."""
        cur = self._cursor()
        cols = ", ".join(_FACT_DAILY_COLUMNS)
        cur.execute(
            f"SELECT {cols} FROM bi_facts.fact_daily WHERE tenant_surrogate_key = %s AND day >= %s AND day <= %s "
            "ORDER BY day",
            (surrogate_key, start, end),
        )
        return tuple(self._hydrate_fact(row) for row in cur.fetchall())

    def _hydrate_fact(self, row: tuple[Any, ...]) -> BIFactDaily:
        (
            fact_daily_id,
            tenant_surrogate_key,
            day,
            revenue_minor,
            usage_call_minutes,
            usage_stt_tokens,
            usage_llm_tokens,
            usage_gpu_seconds,
            ptp_rate,
            recovery_rate,
            compliance_score,
            refreshed_at,
        ) = row
        return BIFactDaily(
            fact_daily_id=str(fact_daily_id),
            tenant_surrogate_key=str(tenant_surrogate_key),
            day=day,
            revenue_minor=revenue_minor,
            usage_call_minutes=usage_call_minutes,
            usage_stt_tokens=usage_stt_tokens,
            usage_llm_tokens=usage_llm_tokens,
            usage_gpu_seconds=usage_gpu_seconds,
            ptp_rate=ptp_rate,
            recovery_rate=recovery_rate,
            compliance_score=compliance_score,
            refreshed_at=refreshed_at,
        )
