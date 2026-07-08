"""Unit tests for src/libs/audit/ (V4 Ch11).

Exercises the hash chain through the real ``AuditRepository`` SQL-building
logic, backed by ``_FakeAuditConnection`` — an in-memory stand-in that
understands the three query shapes ``AuditRepository`` issues (the
``SELECT ... FOR UPDATE`` chain-head lookup, the ``INSERT``, and
``iter_chain``'s ordered ``SELECT``) well enough to actually persist and
replay rows, unlike the plain recorder in ``tests/fixtures/fake_pg.py``.
Real behavioral verification against a live Postgres happens in
tests/integration/ (Phase 2, CPU node).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from src.libs.audit.event import ACTION_PTP_CREATED
from src.libs.audit.logger import AuditLogger
from src.libs.audit.verifier import AuditVerifier
from src.libs.contracts.primitives import TenantId
from src.libs.repositories.audit import _AUDIT_COLUMNS, _CHAIN_COLUMNS, AuditRepository


class _FakeAuditCursor:
    def __init__(self, conn: _FakeAuditConnection) -> None:
        self._conn = conn
        self._last_result: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        rows = self._conn.rows
        if "FOR UPDATE" in sql:
            tenant_id = params[0]
            matches = [row for row in rows if row["tenant_id"] == tenant_id]
            self._last_result = [(matches[-1]["hash"],)] if matches else []
        elif sql.strip().startswith("INSERT INTO"):
            (
                tenant_id,
                actor_id,
                action,
                resource_type,
                resource_id,
                outcome,
                ip_address,
                event_payload,
                prev_hash,
                new_hash,
            ) = params
            seq = len(rows) + 1
            rows.append(
                {
                    "audit_id": f"audit-{seq}",
                    "tenant_id": tenant_id,
                    "actor_id": actor_id,
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "outcome": outcome,
                    "ip_address": ip_address,
                    "event_payload": json.loads(event_payload) if event_payload else None,
                    "recorded_at": datetime.now(UTC),
                    "seq": seq,
                    "prev_hash": prev_hash,
                    "hash": new_hash,
                }
            )
            self._last_result = []
        elif "hash IS NOT NULL" in sql:
            tenant_id = params[0]
            matches = sorted(
                (row for row in rows if row["tenant_id"] == tenant_id and row["hash"] is not None),
                key=lambda row: row["seq"],
            )
            self._last_result = [tuple(row[col] for col in _CHAIN_COLUMNS) for row in matches]
        elif sql.strip().startswith("SELECT"):
            # Generic SELECT shape (e.g. find_by_resource) — filter by tenant_id
            # and, if present, resource_type/resource_id.
            tenant_id = params[0]
            matches = [row for row in rows if row["tenant_id"] == tenant_id]
            if "resource_type = %s AND resource_id = %s" in sql:
                resource_type, resource_id = params[1], params[2]
                matches = [
                    row
                    for row in matches
                    if row["resource_type"] == resource_type and row["resource_id"] == resource_id
                ]
            matches = sorted(matches, key=lambda row: row["seq"], reverse=True)
            self._last_result = [tuple(row[col] for col in _AUDIT_COLUMNS) for row in matches]
        else:
            self._last_result = []

    def fetchone(self) -> Any:
        return self._last_result[0] if self._last_result else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._last_result


class _FakeAuditConnection:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.commit_count = 0

    def cursor(self) -> _FakeAuditCursor:
        return _FakeAuditCursor(self)

    def commit(self) -> None:
        self.commit_count += 1


class TestAuditHashChain:
    def test_audit_hash_chain_valid(self) -> None:
        repo = AuditRepository(_FakeAuditConnection())
        logger = AuditLogger(repo)
        tenant_id = TenantId("tenant-a")

        for i in range(10):
            logger.record_ptp_created(tenant_id, "agent-1", f"ptp-{i}")

        verifier = AuditVerifier(repo)
        result = verifier.verify_chain(tenant_id)

        assert result.valid is True
        assert result.verified_count == 10

    def test_audit_hash_chain_tamper(self) -> None:
        repo = AuditRepository(_FakeAuditConnection())
        logger = AuditLogger(repo)
        tenant_id = TenantId("tenant-a")

        for i in range(10):
            logger.record(tenant_id, "agent-1", ACTION_PTP_CREATED, "PromiseToPay", f"ptp-{i}", "SUCCESS")

        # Tamper with event 5 (1-indexed -> seq 5, list index 4).
        repo._conn.rows[4]["outcome"] = "TAMPERED"

        verifier = AuditVerifier(repo)
        result = verifier.verify_chain(tenant_id)

        assert result.valid is False
        assert result.broken_at_seq == 5

    def test_audit_log_has_no_raw_pii_in_stored_payload(self) -> None:
        repo = AuditRepository(_FakeAuditConnection())
        logger = AuditLogger(repo)
        tenant_id = TenantId("tenant-a")

        logger.record(
            tenant_id,
            "agent-1",
            ACTION_PTP_CREATED,
            "PromiseToPay",
            "ptp-1",
            "SUCCESS",
            event_payload={"note": "customer phone is 9876543210"},
        )

        stored = repo._conn.rows[0]["event_payload"]
        assert "9876543210" not in stored["note"]
        assert "[PHONE]" in stored["note"]


class TestRequiredAuditEvents:
    def test_all_required_event_types_are_recorded(self) -> None:
        repo = AuditRepository(_FakeAuditConnection())
        logger = AuditLogger(repo)
        tenant_id = TenantId("tenant-a")

        logger.record_authn(tenant_id, "agent-1", "SUCCESS")
        logger.record_authn(tenant_id, "agent-1", "FAILURE", failed=True)
        logger.record_authn_logout(tenant_id, "agent-1")
        logger.record_policy_denied(tenant_id, "agent-1", "RBI-001", "outside calling hours")
        logger.record_pii_access(tenant_id, "auditor-1", "Customer", "cust-1")
        logger.record_ptp_created(tenant_id, "agent-1", "ptp-1")
        logger.record_consent_change(tenant_id, "agent-1", "cust-1", granted=True)
        logger.record_consent_change(tenant_id, "agent-1", "cust-1", granted=False)
        logger.record_ai_governance_verdict(tenant_id, "call-1", require_human=False, explanation="LoA violation")
        logger.record_ai_governance_verdict(tenant_id, "call-2", require_human=True, explanation="high risk")
        logger.record_data_erasure(tenant_id, "agent-1", "cust-1")
        logger.record_api_key_operation(tenant_id, "admin-1", "key-1")
        logger.record_api_key_operation(tenant_id, "admin-1", "key-1", revoked=True)

        recorded_actions = {row["action"] for row in repo._conn.rows}
        assert len(repo._conn.rows) == 13
        assert "ai_governance.block" in recorded_actions
        assert "ai_governance.require_human" in recorded_actions
        assert "policy.denied" in recorded_actions


class TestAuditSearch:
    def test_by_resource_returns_the_resources_trail(self) -> None:
        from src.libs.audit.search import AuditSearch

        repo = AuditRepository(_FakeAuditConnection())
        logger = AuditLogger(repo)
        tenant_id = TenantId("tenant-a")
        logger.record_ptp_created(tenant_id, "agent-1", "ptp-1")

        search = AuditSearch(repo)
        events = search.by_resource(tenant_id, "PromiseToPay", "ptp-1")

        assert len(events) == 1
        assert events[0].action == ACTION_PTP_CREATED
