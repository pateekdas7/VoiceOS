#!/usr/bin/env python3
"""Sprint-020 audit hash-chain DR validation — real Postgres.

Referenced by implementation/sprints/Sprint-020.md's DR Validation section:

    python3 scripts/validate/audit_chain.py
    # Expected: AuditVerifier.verify_chain() returns True (no gaps, no tampering)

Appends a handful of real audit events for a validation tenant against a
live Postgres, then walks that tenant's chain end-to-end with
AuditVerifier.verify_chain() (V4 Ch11 §11.7).

    export POSTGRES_DSN='postgresql://user:pass@host:port/db'
    python3 scripts/validate/audit_chain.py
"""

from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import psycopg2

from src.libs.audit.logger import AuditLogger
from src.libs.audit.verifier import AuditVerifier
from src.libs.contracts.primitives import TenantId
from src.libs.repositories.audit import AuditRepository

POSTGRES_DSN = os.environ["POSTGRES_DSN"]


def main() -> int:
    conn = psycopg2.connect(POSTGRES_DSN)
    repo = AuditRepository(conn)
    logger = AuditLogger(repo)
    verifier = AuditVerifier(repo)

    tenant_id = TenantId(str(uuid.uuid4()))
    print(f"[audit_chain] validation tenant={tenant_id}")

    for i in range(10):
        logger.record_ptp_created(tenant_id, "audit-chain-validation", f"ptp-{i}")

    result = verifier.verify_chain(tenant_id)
    print(f"[audit_chain] verify_chain -> valid={result.valid} verified_count={result.verified_count}")
    if not result.valid:
        print(f"[audit_chain] FAIL: chain broke at seq={result.broken_at_seq} audit_id={result.broken_at_audit_id}")
        return 1

    if result.verified_count != 10:
        print(f"[audit_chain] FAIL: expected 10 chained events, verified {result.verified_count}")
        return 1

    print("[audit_chain] PASS: 10/10 events verified, hash chain intact")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
