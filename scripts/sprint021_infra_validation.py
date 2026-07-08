#!/usr/bin/env python3
"""Sprint-021 Phase 2 infrastructure validation — run against the real
Postgres (migration 0018), Redis, and self-hosted Vault Transit (KMS) on
the CPU node.

Exercises the scenarios in implementation/sprints/Sprint-021.md's Phase 2
"Integration validation"/"Infrastructure Validation" sections against real
infrastructure. Not part of the pytest suite (pytest coverage of the same
behaviors lives in tests/unit/services/test_{tenant,org,user}_management.py
and tests/integration/services/test_tenant_isolation.py) — this is an
operational smoke-test / evidence script, following the Sprint-013/015/
016/017/018/019/020 precedent.

Usage:
    VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=<app-token> \\
    POSTGRES_DSN=<dsn> REDIS_URL=<url> \\
    python scripts/sprint021_infra_validation.py
"""

from __future__ import annotations

import datetime
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import redis as redis_lib

from src.libs.contracts.models.customer import Customer
from src.libs.contracts.primitives import CustomerId, TenantId
from src.libs.encryption.kms_client import VaultTransitKMSClient
from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.publisher import Publisher
from src.libs.repositories.customer import CustomerRepository
from src.libs.repositories.organization import OrganizationRepository
from src.libs.repositories.tenant import TenantRepository
from src.libs.repositories.user import UserRepository
from src.services.org_management.service import OrgService
from src.services.tenant_management.lifecycle import InvalidTenantTransitionError
from src.services.tenant_management.provisioner import TENANT_PROVISIONED_EVENT_TYPE, TenantProvisioner
from src.services.tenant_management.service import TenantService
from src.services.tenant_management.suspension import TenantSuspender

VAULT_ADDR = os.environ["VAULT_ADDR"]
VAULT_TOKEN = os.environ["VAULT_TOKEN"]
POSTGRES_DSN = os.environ["POSTGRES_DSN"]
REDIS_URL = os.environ["REDIS_URL"]

_NOW = datetime.datetime.now(datetime.UTC)


def _make_tenant_row(tenant_repo: TenantRepository, slug_prefix: str) -> TenantId:
    from src.libs.contracts.models.tenant import Tenant, TenantStatus

    tenant = tenant_repo.create(
        Tenant(
            tenant_id=TenantId(str(uuid.uuid4())),
            slug=f"{slug_prefix}-{uuid.uuid4().hex[:8]}",
            display_name="Sprint-021 Validation Tenant",
            subscription_tier="GROWTH",
            status=TenantStatus.TRIAL,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    return tenant.tenant_id


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    conn = psycopg2.connect(POSTGRES_DSN)
    redis_conn = redis_lib.Redis.from_url(REDIS_URL, decode_responses=False, protocol=2)
    print(f"Connected to Postgres and Redis — PING: {redis_conn.ping()}")
    print()

    tenant_repo = TenantRepository(conn)
    user_repo = UserRepository(conn)
    org_repo = OrganizationRepository(conn)
    kms = VaultTransitKMSClient(addr=VAULT_ADDR, token=VAULT_TOKEN)
    bus = EventBus(redis_conn)
    publisher = Publisher(bus)

    cleanup_tenant_ids: list[TenantId] = []

    try:
        # 1. Tenant lifecycle: TRIAL -> SANDBOX -> PRODUCTION.
        tenant_id = _make_tenant_row(tenant_repo, "lifecycle")
        cleanup_tenant_ids.append(tenant_id)
        provisioner = TenantProvisioner(
            user_repository=user_repo, kms_client=kms, redis=redis_conn, publisher=publisher
        )
        service = TenantService(tenant_repo, provisioner=provisioner)
        service.move_to_sandbox(tenant_id)
        activated, provisioning_result = service.activate_production(
            tenant_id, f"admin-{uuid.uuid4()}@acme.example", "Validation Admin"
        )
        results.append(
            (
                "Tenant lifecycle TRIAL->SANDBOX->PRODUCTION",
                activated.status.value == "PRODUCTION",
                f"status={activated.status.value}",
            )
        )

        # 2. Invalid transition raises.
        invalid_raised = False
        try:
            service.activate_production(tenant_id, "x@y.com", "X")
        except InvalidTenantTransitionError:
            invalid_raised = True
        results.append(("Invalid lifecycle transition raises", invalid_raised, f"raised={invalid_raised}"))

        # 3. TenantProvisioner creates KEK in real Vault Transit.
        kek_id = provisioning_result.kek_id
        key_exists = True
        try:
            kms._client.secrets.transit.read_key(name=kek_id, mount_point="transit")
        except Exception:
            key_exists = False
        results.append(("TenantProvisioner creates KEK in Vault Transit", key_exists, f"kek_id={kek_id}"))

        # 4. TenantProvisioned event delivered via real Redis EventBus.
        entries = bus.replay_from()
        delivered = any(
            envelope.event_type == TENANT_PROVISIONED_EVENT_TYPE and str(envelope.tenant_id) == str(tenant_id)
            for _entry_id, envelope in entries
        )
        results.append(("TenantProvisioned event delivered via real EventBus", delivered, f"found={delivered}"))

        # 5. Cross-tenant isolation: customer in tenant-A not visible from tenant-B.
        customer_repo = CustomerRepository(conn)
        tenant_a = _make_tenant_row(tenant_repo, "iso-a")
        tenant_b = _make_tenant_row(tenant_repo, "iso-b")
        cleanup_tenant_ids.extend([tenant_a, tenant_b])
        customer_id = CustomerId(str(uuid.uuid4()))
        customer_repo.create(
            Customer(
                customer_id=customer_id,
                tenant_id=tenant_a,
                crm_id=f"crm-{uuid.uuid4()}",
                name="Isolation Test Customer",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        visible_a = customer_repo.get(tenant_a, customer_id) is not None
        visible_b = customer_repo.get(tenant_b, customer_id) is not None
        results.append(
            (
                "Cross-tenant isolation: tenant-B query returns 0 results",
                visible_a and not visible_b,
                f"visible_own_tenant={visible_a} visible_other_tenant={visible_b}",
            )
        )
        conn.cursor().execute("DELETE FROM customers WHERE customer_id = %s", (customer_id,))
        conn.commit()

        # 6. Org scope: BU-level user sees branch; branch cannot see sibling.
        org_service = OrgService(org_repo)
        org = org_service.create_organization(tenant_a, "Acme Collections Pvt Ltd")
        bu = org_service.add_business_unit(tenant_a, org.org_id, "Retail Collections")
        branch_1 = org_service.add_branch(tenant_a, bu.bu_id, "Mumbai South")
        branch_2 = org_service.add_branch(tenant_a, bu.bu_id, "Mumbai North")
        hierarchy = org_service.get_hierarchy(tenant_a)
        assert hierarchy is not None
        from src.libs.contracts.models.user import OrgScope

        bu_sees_branch = hierarchy.resolve_scope(
            OrgScope(scope_type="BUSINESS_UNIT", scope_id=bu.bu_id),
            OrgScope(scope_type="BRANCH", scope_id=branch_1.branch_id),
        )
        branch_sees_sibling = hierarchy.resolve_scope(
            OrgScope(scope_type="BRANCH", scope_id=branch_1.branch_id),
            OrgScope(scope_type="BRANCH", scope_id=branch_2.branch_id),
        )
        results.append(
            (
                "OrgHierarchy: BU sees branch, branch cannot see sibling",
                bu_sees_branch and not branch_sees_sibling,
                f"bu_sees_branch={bu_sees_branch} branch_sees_sibling={branch_sees_sibling}",
            )
        )

        # 7. TenantSuspender: suspension blocks new call admission.
        suspender = TenantSuspender(tenant_repo)
        before_suspend = suspender.is_call_admission_allowed(tenant_id)
        suspender.suspend(tenant_id)
        after_suspend = suspender.is_call_admission_allowed(tenant_id)
        results.append(
            (
                "TenantSuspender blocks new call admission",
                before_suspend and not after_suspend,
                f"before={before_suspend} after={after_suspend}",
            )
        )
    finally:
        for tid in cleanup_tenant_ids:
            cur = conn.cursor()
            cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tid,))
        conn.commit()
        conn.close()

    print(f"{'CHECK':<65} {'RESULT':<8} DETAIL")
    print("-" * 115)
    all_ok = True
    for name, ok, detail in results:
        status_str = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        print(f"{name:<65} {status_str:<8} {detail}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
