"""Unit tests for Phase 6d: ComplianceMonitoring with durable violation repository.

Verifies:
- Backward compatibility: existing tests still pass (no repo = in-memory fallback)
- With repo: violations are persisted on ingest()
- With repo: status() reads from DB, not in-memory set
- Idempotency: repeated ingestion of same rule does not create duplicate violations
- Re-detection: resolved violations flip back to ACTIVE on re-detection
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from src.libs.audit.event import AuditEvent
from src.libs.contracts.primitives import TenantId
from src.services.compliance_monitoring.service import ComplianceMonitoring, ComplianceStatus


def _consent_denied_event(tenant_id: str, seq: int = 0) -> AuditEvent:
    return AuditEvent(
        audit_id=f"audit-{seq}",
        tenant_id=tenant_id,
        actor_id="policy_engine",
        action="consent.denied",
        resource_type="Consent",
        resource_id=f"cust-{seq}",
        outcome="DENY",
        recorded_at=datetime.now(UTC),
    )


class _FakeViolationRepository:
    """In-memory fake that mimics ComplianceViolationRepository for unit tests."""

    def __init__(self) -> None:
        self.active: dict[tuple[str, str], str] = {}  # (tenant_id, rule_id) -> signal_summary
        self.upsert_calls: list[tuple[str, str, str]] = []

    def upsert_active(self, tenant_id: TenantId, rule_id: str, signal_summary: str) -> None:
        key = (str(tenant_id), rule_id)
        self.active[key] = signal_summary
        self.upsert_calls.append((str(tenant_id), rule_id, signal_summary))

    def is_violated(self, tenant_id: TenantId) -> bool:
        return any(tid == str(tenant_id) for (tid, _) in self.active)

    def resolve(self, tenant_id: TenantId, rule_id: str) -> None:
        key = (str(tenant_id), rule_id)
        self.active.pop(key, None)


class TestBackwardCompatibility:
    """Existing tests must pass unchanged when no repository is wired."""

    def test_status_compliant_without_repo(self) -> None:
        monitoring = ComplianceMonitoring()
        assert monitoring.status("tenant-a") == ComplianceStatus.COMPLIANT

    def test_status_violation_after_five_events(self) -> None:
        monitoring = ComplianceMonitoring()
        for i in range(5):
            monitoring.ingest(_consent_denied_event("tenant-a", i))
        assert monitoring.status("tenant-a") == ComplianceStatus.VIOLATION

    def test_four_events_not_a_violation(self) -> None:
        monitoring = ComplianceMonitoring()
        for i in range(4):
            monitoring.ingest(_consent_denied_event("tenant-a", i))
        assert monitoring.status("tenant-a") == ComplianceStatus.COMPLIANT

    def test_factory_create_no_args(self) -> None:
        monitoring = ComplianceMonitoring.create()
        assert monitoring.status("t-x") == ComplianceStatus.COMPLIANT


class TestWithRepository:
    def test_violation_is_persisted_to_repo_on_signal(self) -> None:
        repo = _FakeViolationRepository()
        monitoring = ComplianceMonitoring.create(violation_repository=repo)
        for i in range(5):
            monitoring.ingest(_consent_denied_event("tenant-a", i))

        assert len(repo.upsert_calls) == 1
        assert repo.upsert_calls[0][0] == "tenant-a"

    def test_status_reads_from_repo_not_memory(self) -> None:
        repo = _FakeViolationRepository()
        monitoring = ComplianceMonitoring.create(violation_repository=repo)
        # Force a violation directly into repo without ingest()
        repo.upsert_active(TenantId("tenant-z"), "test-rule", "manual insert")
        # status() should delegate to repo
        assert monitoring.status("tenant-z") == ComplianceStatus.VIOLATION

    def test_status_compliant_when_repo_has_no_active(self) -> None:
        repo = _FakeViolationRepository()
        monitoring = ComplianceMonitoring.create(violation_repository=repo)
        assert monitoring.status("tenant-q") == ComplianceStatus.COMPLIANT

    def test_idempotency_same_signal_twice(self) -> None:
        repo = _FakeViolationRepository()
        monitoring = ComplianceMonitoring.create(violation_repository=repo)
        # First burst — crosses threshold at event 5
        for i in range(5):
            monitoring.ingest(_consent_denied_event("tenant-a", i))
        # After threshold is crossed the window resets, so 4 more won't cross it again
        for i in range(5, 9):
            monitoring.ingest(_consent_denied_event("tenant-a", i))
        # Only one upsert_active call expected (only one threshold crossing)
        assert len(repo.upsert_calls) == 1

    def test_re_detection_after_resolve(self) -> None:
        repo = _FakeViolationRepository()
        monitoring = ComplianceMonitoring.create(violation_repository=repo)
        # Trigger violation
        for i in range(5):
            monitoring.ingest(_consent_denied_event("tenant-a", i))
        assert monitoring.status("tenant-a") == ComplianceStatus.VIOLATION
        # Resolve it
        repo.resolve(TenantId("tenant-a"), repo.upsert_calls[0][1])
        assert monitoring.status("tenant-a") == ComplianceStatus.COMPLIANT
        # Re-trigger — should upsert_active again
        for i in range(10, 15):
            monitoring.ingest(_consent_denied_event("tenant-a", i))
        assert len(repo.upsert_calls) == 2

    def test_tenant_isolation_different_tenants_independent(self) -> None:
        repo = _FakeViolationRepository()
        monitoring = ComplianceMonitoring.create(violation_repository=repo)
        for i in range(5):
            monitoring.ingest(_consent_denied_event("tenant-a", i))
        # tenant-b has no events
        assert monitoring.status("tenant-a") == ComplianceStatus.VIOLATION
        assert monitoring.status("tenant-b") == ComplianceStatus.COMPLIANT

    def test_rules_returns_active_rules(self) -> None:
        monitoring = ComplianceMonitoring.create()
        assert isinstance(monitoring.rules(), tuple)
        assert len(monitoring.rules()) > 0
