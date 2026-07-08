"""Snapshot — periodic Recoverable state serialization to Postgres (V3 Ch6).

Backs crash recovery: every ``take_snapshot()`` call persists the component's
current state; ``load_latest_snapshot()`` retrieves the highest-versioned row
for a call so ``EventTailReplay`` (replay.py) can resume from it.

Architecture: V3 Ch6 §snapshots table.
"""

from __future__ import annotations

import json
from typing import Any

from src.libs.contracts.primitives import CallId, TenantId
from src.libs.repositories.base import BaseRepository

from .recoverable import Recoverable, StateSnapshot

_TABLE = "snapshots"


class Snapshot(BaseRepository):
    """Tenant-scoped snapshot persistence for any ``Recoverable`` component."""

    def take_snapshot(self, component: Recoverable, tenant_id: TenantId, call_id: CallId) -> StateSnapshot:
        """Serialize ``component`` and durably persist the snapshot.

        Args:
            component: The Recoverable whose current state is captured.
            tenant_id: Tenant scope (AR-8).
            call_id: The call this snapshot belongs to.

        Returns:
            The StateSnapshot that was persisted (as returned by
            ``component.snapshot()``).
        """
        state_snapshot = component.snapshot()
        self._execute(
            f"""
            INSERT INTO {_TABLE} (tenant_id, call_id, version, state, last_event_offset, created_at)
            VALUES (%s, %s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (call_id, version) DO NOTHING
            """,
            (
                tenant_id,
                call_id,
                state_snapshot.version,
                json.dumps(state_snapshot.state),
                state_snapshot.last_event_offset,
                state_snapshot.created_at,
            ),
        )
        self._commit()
        return state_snapshot

    def load_latest_snapshot(self, tenant_id: TenantId, call_id: CallId) -> StateSnapshot | None:
        """Return the highest-versioned snapshot for ``call_id``, or ``None``."""
        rows = self._tenant_select(
            _TABLE,
            ("version", "state", "last_event_offset", "created_at"),
            tenant_id,
            extra_where="call_id = %s",
            extra_params=(call_id,),
            order_by="version DESC",
            limit=1,
        )
        if not rows:
            return None
        version, state, last_event_offset, created_at = rows[0]
        state_dict: dict[str, Any] = json.loads(state) if isinstance(state, str) else state
        return StateSnapshot(
            call_id=call_id,
            version=version,
            state=state_dict,
            last_event_offset=last_event_offset,
            created_at=created_at,
        )
