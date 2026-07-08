"""RecoveryLog — auditable record of every crash-recovery attempt (V3 Ch7).

Persisted to Postgres so operators can audit whether a given call's recovery
succeeded or failed, which strategy ran, and how long it took — the
``recovery_log`` health check referenced by CPU_NODE_STATE.md.

Architecture: V3 Ch7 (Crash Recovery).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.libs.contracts.primitives import CallId, TenantId
from src.libs.repositories.base import BaseRepository

_TABLE = "recovery_log"


class RecoveryAttempt(BaseModel):
    """One recorded recovery attempt for a single call."""

    model_config = ConfigDict(frozen=True)

    call_id: CallId
    failure_class: str
    """Which failure class triggered recovery: 'cpu_restart' | 'gpu_failure' |
    'redis_outage' | 'db_outage' | 'twilio_disconnect' | 'network_partition'."""
    strategy_name: str
    outcome: Literal["success", "failure"]
    detail: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    completed_at: datetime
    duration_ms: int = Field(ge=0)


class RecoveryLog(BaseRepository):
    """Tenant-scoped append log of recovery attempts."""

    def record(self, tenant_id: TenantId, attempt: RecoveryAttempt) -> None:
        """Persist ``attempt`` to the recovery_log table."""
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                tenant_id, call_id, failure_class, strategy_name, outcome,
                detail, started_at, completed_at, duration_ms, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, NOW())
            """,
            (
                tenant_id,
                attempt.call_id,
                attempt.failure_class,
                attempt.strategy_name,
                attempt.outcome,
                json.dumps(attempt.detail),
                attempt.started_at,
                attempt.completed_at,
                attempt.duration_ms,
            ),
        )
        self._commit()

    def last_outcome(self, tenant_id: TenantId, call_id: CallId) -> str | None:
        """Return the most recent recovery outcome for ``call_id``, or ``None``."""
        rows = self._tenant_select(
            _TABLE,
            ("outcome",),
            tenant_id,
            extra_where="call_id = %s",
            extra_params=(call_id,),
            order_by="created_at DESC",
            limit=1,
        )
        return str(rows[0][0]) if rows else None
