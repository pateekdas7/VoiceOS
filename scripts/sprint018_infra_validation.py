#!/usr/bin/env python3
"""Sprint-018 Phase 2 infrastructure validation — run against real Redis + the
provisioned mTLS PKI on the CPU node.

Exercises the scenarios listed in implementation/sprints/Sprint-018.md
Phase 2 "Integration validation" section against live Redis (EventBus) and
the real certificates written by scripts/pki/generate_mtls_certs.py. Not
part of the pytest suite (pytest coverage of the same behaviors lives in
tests/unit/services/test_{auth,authz,ai_governance}.py and
tests/integration/services/test_auth_integration.py) — this is an
operational smoke-test / evidence script for the sprint completion report,
following the Sprint-013/015/016/017 precedent.

No GPU node dependency — Auth/Authz/AI-Governance are CPU services.

Usage:
    REDIS_URL=redis://localhost:6379/0 \\
    MTLS_CERTS_DIR=/opt/voiceos/certs \\
    python scripts/sprint018_infra_validation.py
"""

from __future__ import annotations

import datetime
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis as redis_lib
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from src.libs.contracts.response_plan import ResponsePlan, StrategyAction, StrategyLabel
from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.publisher import Publisher
from src.services.ai_governance import metrics as governance_metrics
from src.services.ai_governance.governance_layer import HUMAN_REVIEW_REQUIRED_EVENT_TYPE
from src.services.ai_governance.service import AIGovernanceService
from src.services.ai_governance.verdict import GovernanceStatus
from src.services.auth.jwt_validator import JWTValidator, issue_test_token
from src.services.auth.models import AuthenticationError
from src.services.auth.mtls_enforcer import MTLSEnforcer
from src.services.auth.service import AuthService
from src.services.authz.models import AuthorizationOutcome, AuthorizationRequest
from src.services.authz.roles import Role
from src.services.authz.service import AuthzService
from src.services.authz.tenant_isolation import TenantIsolationViolationError

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
MTLS_CERTS_DIR = Path(os.environ.get("MTLS_CERTS_DIR", "/opt/voiceos/certs"))


def _response_plan(facts: dict[str, object] | None = None) -> ResponsePlan:
    return ResponsePlan(
        plan_id=str(uuid.uuid4()),
        version=1,
        call_id=f"validation-{uuid.uuid4()}",
        tenant_id="tenant-validation",
        created_at=datetime.datetime.now(datetime.UTC),
        strategy=StrategyAction(action=StrategyLabel.ASK),
        facts=facts or {},  # type: ignore[arg-type]
    )


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    redis_conn = redis_lib.Redis.from_url(REDIS_URL, decode_responses=False, protocol=2)
    print(f"Connected to Redis — PING: {redis_conn.ping()}")
    print(f"Reading mTLS PKI from {MTLS_CERTS_DIR}")
    print()

    # 1. JWT valid -> AuthContext with correct tenant_id/role.
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwt_validator = JWTValidator(public_key=private_key.public_key())
    token = issue_test_token(private_key, subject="agent-1", tenant_id="tenant-validation", role=Role.AGENT.value)
    auth_context = jwt_validator.validate(token)
    results.append(
        (
            "JWT valid signature -> AuthContext populated",
            auth_context.tenant_id == "tenant-validation" and auth_context.role == "AGENT",
            f"tenant_id={auth_context.tenant_id} role={auth_context.role}",
        )
    )

    # 2. JWT invalid signature -> AuthenticationError (401-equivalent).
    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged_token = issue_test_token(other_private_key, subject="agent-1", tenant_id="tenant-validation")
    try:
        jwt_validator.validate(forged_token)
        jwt_rejected = False
    except AuthenticationError:
        jwt_rejected = True
    results.append(("JWT invalid signature -> AuthenticationError (401)", jwt_rejected, f"rejected={jwt_rejected}"))

    # 3. API key without header -> AuthenticationError (401-equivalent).
    auth_service = AuthService()
    try:
        auth_service.authenticate()
        no_creds_rejected = False
    except AuthenticationError:
        no_creds_rejected = True
    results.append(
        ("Request with no credentials -> AuthenticationError (401)", no_creds_rejected, f"rejected={no_creds_rejected}")
    )

    # 4. RBAC: AUDITOR + DELETE -> 403-equivalent DENY.
    auditor_context = jwt_validator.validate(
        issue_test_token(private_key, subject="auditor-1", tenant_id="tenant-validation", role=Role.AUDITOR.value)
    )
    authz_service = AuthzService()
    authz_result = authz_service.authorize(
        AuthorizationRequest(auth_context=auditor_context, action="DELETE", resource_tenant_id="tenant-validation")
    )
    results.append(
        (
            "RBAC: AUDITOR + DELETE -> DENY (403)",
            authz_result.outcome == AuthorizationOutcome.DENY,
            f"outcome={authz_result.outcome.value}",
        )
    )

    # 5. Tenant isolation: cross-tenant request -> TenantIsolationViolationError (403).
    try:
        authz_service.authorize(
            AuthorizationRequest(auth_context=auth_context, action="GET", resource_tenant_id="tenant-OTHER")
        )
        tenant_isolation_blocked = False
    except TenantIsolationViolationError:
        tenant_isolation_blocked = True
    results.append(
        (
            "Tenant isolation: cross-tenant request -> TenantIsolationViolationError (403)",
            tenant_isolation_blocked,
            f"blocked={tenant_isolation_blocked}",
        )
    )

    # 6. mTLS: real CA-signed client cert -> AuthContext.
    ca_cert_pem = (MTLS_CERTS_DIR / "ca.crt").read_bytes()
    client_cert_pem = (MTLS_CERTS_DIR / "policy-engine-service.crt").read_bytes()
    mtls_enforcer = MTLSEnforcer(ca_certificate_pem=ca_cert_pem)
    mtls_context = mtls_enforcer.validate_client_cert(client_cert_pem)
    results.append(
        (
            "mTLS: real CA-signed service cert -> AuthContext",
            mtls_context.subject == "policy-engine-service",
            f"subject={mtls_context.subject}",
        )
    )

    # 7. mTLS: cert from a rogue (non-CA-signed) source is rejected.
    rogue_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    try:
        # A self-signed cert reusing the same subject, signed by a key the
        # configured CA never issued — must not verify.
        now = datetime.datetime.now(datetime.UTC)
        rogue_cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "policy-engine-service")]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "policy-engine-service")]))
            .public_key(rogue_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30))
            .sign(rogue_key, hashes.SHA256())
        )
        mtls_enforcer.validate_client_cert(rogue_cert.public_bytes(serialization.Encoding.PEM))
        rogue_rejected = False
    except AuthenticationError:
        rogue_rejected = True
    results.append(("mTLS: non-CA-signed cert rejected", rogue_rejected, f"rejected={rogue_rejected}"))

    # 8. AI Governance: hallucinated fact -> BLOCK verdict (real Redis EventBus audit).
    bus = EventBus(redis_conn, stream="voiceos-events")
    publisher = Publisher(bus)
    governance_service = AIGovernanceService.create(publisher=publisher)
    plan = _response_plan(facts={"outstanding_balance_minor": 1_250_000})  # ₹12,500 authoritative
    violations_before = governance_metrics.LAW_OF_AUTHORITY_VIOLATIONS._value.get()
    block_verdict = governance_service.evaluate_output(
        "Aapka bakaya ₹99,999 hai.", plan, call_id=plan.call_id, tenant_id=plan.tenant_id
    )
    violations_after = governance_metrics.LAW_OF_AUTHORITY_VIOLATIONS._value.get()
    results.append(
        (
            "AI Governance: hallucinated fact -> BLOCK verdict",
            block_verdict.status == GovernanceStatus.BLOCK,
            f"status={block_verdict.status.value}",
        )
    )
    results.append(
        (
            "law_of_authority_violations counter increments on BLOCK",
            violations_after == violations_before + 1,
            f"before={violations_before} after={violations_after}",
        )
    )

    # 9. AI Governance: clean output (all facts match) -> APPROVE verdict.
    approve_verdict = governance_service.evaluate_output(
        "Aapka bakaya ₹12,500 hai.", plan, call_id=plan.call_id, tenant_id=plan.tenant_id
    )
    results.append(
        (
            "AI Governance: grounded fact -> APPROVE verdict",
            approve_verdict.status == GovernanceStatus.APPROVE,
            f"status={approve_verdict.status.value}",
        )
    )

    # 10. REQUIRE_HUMAN -> supervisor queue event emitted (real EventBus/Redis).
    require_human_verdict = governance_service.evaluate_output(
        "Sab kuch theek hai.", plan, risk_score=0.95, call_id=plan.call_id, tenant_id=plan.tenant_id
    )
    supervisor_events = [
        e
        for _id, e in bus.replay_from()
        if e.event_type == HUMAN_REVIEW_REQUIRED_EVENT_TYPE and e.payload.get("call_id") == plan.call_id
    ]
    results.append(
        (
            "REQUIRE_HUMAN verdict -> supervisor queue event emitted (real EventBus)",
            require_human_verdict.status == GovernanceStatus.REQUIRE_HUMAN and len(supervisor_events) == 1,
            f"status={require_human_verdict.status.value} events_found={len(supervisor_events)}",
        )
    )

    print(f"{'CHECK':<70} {'RESULT':<8} DETAIL")
    print("-" * 115)
    all_ok = True
    for name, ok, detail in results:
        status_str = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        print(f"{name:<70} {status_str:<8} {detail}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
