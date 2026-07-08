#!/usr/bin/env python3
"""Sprint-025 Phase 2 infrastructure validation — run against the real
Postgres (migration 0024) on the CPU node.

Exercises the scenarios in implementation/sprints/Sprint-025.md's Phase 2
"Integration validation"/"Infrastructure Validation" sections against real
infrastructure. Not part of the pytest suite (pytest coverage of the same
behaviors lives in tests/unit/services/test_{admin_portal,ai_config,
webhooks,public_api}.py) — this is an operational smoke-test / evidence
script, following the Sprint-013/.../024 precedent.

Webhook delivery uses a real local HTTP server (stdlib http.server on a
background thread) so the signature-verification check exercises an actual
HTTP round trip, not an in-process fake — same spirit as Sprint-025.md
Phase 1's "aiohttp.web test server", swapped for the stdlib http.server
already used elsewhere in this repo (no new dependency).

Usage:
    POSTGRES_DSN=<dsn> python scripts/sprint025_infra_validation.py
"""

from __future__ import annotations

import os
import sys
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

from src.libs.contracts.models.ai_config import ModelConfig
from src.libs.contracts.models.campaign import AudienceCriteria, Campaign, RetryPolicy
from src.libs.contracts.models.integration import APIKeyRecord as PersistedAPIKeyRecord
from src.libs.contracts.models.tenant import Tenant
from src.libs.contracts.primitives import CampaignId, TenantId
from src.libs.repositories.admin_audit_view import AdminAuditViewRepository
from src.libs.repositories.ai_config import ModelConfigRepository, PromptVersionRepository
from src.libs.repositories.campaign import CampaignRepository
from src.libs.repositories.idempotency import IdempotencyRepository
from src.libs.repositories.integration import (
    APIKeyRepository,
    APIKeyUsageRepository,
    APIRateLimitRepository,
    WebhookDeliveryAttemptRepository,
    WebhookDeliveryRepository,
    WebhookDLQRepository,
    WebhookRegistrationRepository,
)
from src.libs.repositories.tenant import TenantRepository
from src.services.admin_portal.campaign_admin import CampaignAdminController, CampaignApprovalDeniedError
from src.services.ai_config.model_config import ModelConfigService
from src.services.ai_config.prompt_versioning import PromptImmutableError, PromptVersioningService
from src.services.api_platform.api_key_lifecycle import APIKeyLifecycleService
from src.services.auth.api_key_validator import APIKeyValidator
from src.services.campaign_management.service import CampaignPromptNotPinnedError, CampaignService
from src.services.integration_platform.delivery import WebhookDeliveryEngine
from src.services.integration_platform.signature import SIGNATURE_HEADER, WebhookSigner
from src.services.integration_platform.webhook import WebhookService
from src.services.policy_engine.service import PolicyEngineService
from src.services.user_management.sso_stub import SSOIntegration

POSTGRES_DSN = os.environ["POSTGRES_DSN"]
_NOW = datetime.now(UTC)

_received: dict[str, object] = {}


class _CaptureHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        _received["body"] = body
        _received["signature"] = self.headers.get(SIGNATURE_HEADER, "")
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    conn = psycopg2.connect(POSTGRES_DSN)
    print(f"Connected to Postgres: {POSTGRES_DSN.split('@')[-1]}")

    tenant_id = TenantId(str(uuid.uuid4()))
    server = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    webhook_url = f"http://127.0.0.1:{server.server_port}/hook"

    try:
        TenantRepository(conn).create(
            Tenant(
                tenant_id=tenant_id,
                slug=f"sprint025-validation-{uuid.uuid4().hex[:8]}",
                display_name="Sprint-025 Validation Tenant",
                subscription_tier="GROWTH",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )

        # 1-2. Prompt versioning: create -> hash matches -> publish -> immutable.
        prompt_repo = PromptVersionRepository(conn)
        prompt_service = PromptVersioningService(prompt_repo)
        template = "Negotiate a payment plan with the customer, referencing their outstanding balance."
        version = prompt_service.create_version(tenant_id, "collections_negotiation", template)
        results.append(
            (
                "Prompt version: hash == sha256(template)",
                version.hash == PromptVersioningService.hash_template(template),
                f"hash={version.hash[:16]}...",
            )
        )
        published = prompt_service.publish(tenant_id, version.prompt_version_id)
        try:
            prompt_service.edit(tenant_id, version.prompt_version_id, "an edited template")
            immutability_held = False
            detail = "edit() did not raise"
        except PromptImmutableError as exc:
            immutability_held = True
            detail = str(exc)
        results.append(("Prompt version: publish -> edit -> raises PromptImmutableError", immutability_held, detail))
        results.append(
            ("Prompt version: status PUBLISHED persisted", published.status.value == "PUBLISHED", published.status)
        )

        # 3. Model config inheritance: campaign override wins over tenant default.
        # campaign_id is a real UUID FK to campaigns.campaign_id (migration 0024) --
        # a synthetic non-UUID id here would hit the same "non-UUID id in a
        # validation script" bug class as Sprint-016/021/023/024's own first-
        # real-Postgres-run fixes, so a real Campaign row is created first.
        campaign_repo = CampaignRepository(conn)
        campaign_id = CampaignId(str(uuid.uuid4()))
        campaign_repo.create(
            Campaign(
                campaign_id=campaign_id,
                tenant_id=tenant_id,
                name="Sprint-025 Validation Campaign",
                audience_criteria=AudienceCriteria(),
                retry_policy=RetryPolicy(),
                created_at=_NOW,
                updated_at=_NOW,
                created_by="validator",
            )
        )
        model_repo = ModelConfigRepository(conn)
        model_service = ModelConfigService(model_repo)
        model_service.configure(tenant_id, ModelConfig(model_config_id="", tenant_id=tenant_id, llm_temperature=0.5))
        model_service.configure(
            tenant_id, ModelConfig(model_config_id="", tenant_id=tenant_id, llm_temperature=0.9), campaign_id
        )
        resolved = model_service.resolve(tenant_id, campaign_id)
        results.append(
            (
                "Model config: campaign override (0.9) wins over tenant default (0.5)",
                resolved.llm_temperature == 0.9,
                f"resolved llm_temperature={resolved.llm_temperature}",
            )
        )

        # 3b. Admin Portal gap-fill: PolicyEngine-validated campaign approval --
        # the just-created campaign is still DRAFT (never submitted for review),
        # so approval must be denied by AdminPolicyPack.CAMPAIGN_APPROVAL_REQUIRES_REVIEW.
        class _FakeCampaignServiceForApproval:
            def get(self, t: TenantId, c: CampaignId) -> Campaign | None:
                return campaign_repo.get(t, c)

            def approve(self, t: TenantId, c: CampaignId, approved_by: str) -> Campaign:
                raise AssertionError("approve() must not be reached when the Policy Engine denies")

        campaign_admin = CampaignAdminController(_FakeCampaignServiceForApproval(), PolicyEngineService.create())  # type: ignore[arg-type]
        try:
            campaign_admin.approve(tenant_id, campaign_id, "validator")
            approval_denied = False
        except CampaignApprovalDeniedError:
            approval_denied = True
        results.append(
            (
                "Admin Portal: PolicyEngine denies approval of an unreviewed (DRAFT) campaign",
                approval_denied,
                f"denied={approval_denied}",
            )
        )

        # 3c. Admin Portal gap-fill: SSO configuration persists to the real sso_config table.
        # provider is constrained by ck_sso_config_provider_enum (migration 0018) to
        # NONE/SAML/OIDC -- a protocol, not a vendor name (e.g. "okta" is an OIDC provider).
        SSOIntegration(conn).configure(str(tenant_id), "OIDC", {"issuer": "https://validation.okta.example.com"})
        cur = conn.cursor()
        cur.execute("SELECT provider, enabled FROM sso_config WHERE tenant_id = %s", (str(tenant_id),))
        sso_row = cur.fetchone()
        results.append(
            (
                "Admin Portal: SSO configuration persisted to sso_config (enabled=false, stub)",
                sso_row is not None and sso_row[0] == "OIDC" and sso_row[1] is False,
                f"row={sso_row}",
            )
        )

        # 4. Webhook: endpoint registered -> real HTTP POST received -> signature valid.
        webhook_reg_repo = WebhookRegistrationRepository(conn)
        webhook_delivery_repo = WebhookDeliveryRepository(conn)
        attempt_repo = WebhookDeliveryAttemptRepository(conn)
        dlq_repo = WebhookDLQRepository(conn)
        idempotency_repo = IdempotencyRepository(conn)
        delivery_engine = WebhookDeliveryEngine(
            _HttpxLikeClient(), webhook_delivery_repo, attempt_repository=attempt_repo, dlq_repository=dlq_repo
        )
        webhook_service = WebhookService(webhook_reg_repo, delivery_engine, idempotency_repository=idempotency_repo)
        registration = webhook_service.register_endpoint(
            tenant_id, webhook_url, ("call.completed",), "sprint025-validation-secret"
        )
        payload: dict[str, object] = {
            "tenant_id": str(tenant_id),
            "call_id": "call-validation-1",
            "outcome_code": "PTP_MADE",
        }
        webhook_service.dispatch("call.completed", payload)

        server_thread.join(timeout=2.0)
        received_signature = str(_received.get("signature", ""))
        signature_valid = bool(received_signature) and WebhookSigner.verify(
            payload, registration.secret, received_signature
        )
        results.append(
            (
                "Webhook: real HTTP POST received with valid X-VoiceOS-Signature",
                signature_valid,
                f"signature={received_signature[:24]}...",
            )
        )

        # 5. Webhook retry: 3 failed attempts against an unreachable port -> DLQ entry.
        dead_engine = WebhookDeliveryEngine(
            _HttpxLikeClient(),
            webhook_delivery_repo,
            attempt_repository=attempt_repo,
            dlq_repository=dlq_repo,
            sleep_fn=lambda _s: None,
        )
        dead_registration = registration.model_copy(update={"url": "http://127.0.0.1:1"})
        dlq_delivery = dead_engine.deliver(dead_registration, "call.completed", payload)
        results.append(
            (
                "Webhook retry: 3 failed attempts -> DLQ entry created",
                dlq_delivery.status.value == "DLQ" and dlq_delivery.attempt == 3,
                f"status={dlq_delivery.status.value} attempt={dlq_delivery.attempt}",
            )
        )

        # 5b. Webhook management (Sprint-025 Part-2 gap-fill): update, delivery history,
        # DLQ listing, secret rotation, deactivation -- all against real Postgres.
        history = delivery_engine.history(tenant_id, registration.webhook_id)
        # 2 rows expected: the earlier successful delivery, plus the DLQ delivery above
        # (dead_registration.model_copy() keeps the same webhook_id -- only the URL differs).
        history_statuses = {d.status.value for d in history}
        results.append(
            (
                "Webhook: delivery_history returns both the DELIVERED and DLQ attempts",
                len(history) == 2 and history_statuses == {"DELIVERED", "DLQ"},
                f"count={len(history)} statuses={sorted(history_statuses)}",
            )
        )

        dlq_entries = delivery_engine.dlq(tenant_id)
        results.append(
            (
                "Webhook: dlq() returns the DLQ entry from the retry-exhaustion check",
                len(dlq_entries) == 1 and dlq_entries[0].status.value == "DLQ",
                f"count={len(dlq_entries)}",
            )
        )

        webhook_service.update_endpoint(
            tenant_id, registration.webhook_id, url="https://updated.example.com/hook", event_types=("ptp.created",)
        )
        updated_registration = webhook_reg_repo.get(tenant_id, registration.webhook_id)
        results.append(
            (
                "Webhook: update_endpoint persists new url/event_types",
                updated_registration is not None
                and updated_registration.url == "https://updated.example.com/hook"
                and updated_registration.event_types == ("ptp.created",),
                f"url={updated_registration.url if updated_registration else None}",
            )
        )

        old_secret = registration.secret
        new_secret = webhook_service.rotate_secret(tenant_id, registration.webhook_id)
        rotated_registration = webhook_reg_repo.get(tenant_id, registration.webhook_id)
        results.append(
            (
                "Webhook: rotate_secret persists a new, different secret",
                rotated_registration is not None
                and rotated_registration.secret == new_secret
                and new_secret != old_secret,
                f"secret_changed={new_secret != old_secret}",
            )
        )

        webhook_service.deactivate_endpoint(tenant_id, registration.webhook_id)
        deactivated_registration = webhook_reg_repo.get(tenant_id, registration.webhook_id)
        results.append(
            (
                "Webhook: deactivate_endpoint persists is_active=False",
                deactivated_registration is not None and deactivated_registration.is_active is False,
                f"is_active={deactivated_registration.is_active if deactivated_registration else None}",
            )
        )

        # 5c. Part-3: webhook_delivery_attempts -- one row per individual HTTP attempt
        # (1 success from the signature check + 3 failures from the retry check = 4).
        attempts = attempt_repo.list_for_webhook(tenant_id, registration.webhook_id)
        results.append(
            (
                "Part-3: webhook_delivery_attempts recorded one row per HTTP attempt",
                len(attempts) == 4 and sum(1 for a in attempts if a.succeeded) == 1,
                f"count={len(attempts)} succeeded={sum(1 for a in attempts if a.succeeded)}",
            )
        )

        # 5d. Part-3: dedicated webhook_dead_letter_queue store (separate from
        # webhook_deliveries.status == DLQ).
        dlq_table_entries = dlq_repo.list_for_tenant(tenant_id)
        results.append(
            (
                "Part-3: webhook_dead_letter_queue has the exhausted delivery",
                len(dlq_table_entries) == 1 and dlq_table_entries[0].attempts == 3,
                f"count={len(dlq_table_entries)}",
            )
        )

        # 5e. Part-3: EventBus -> webhook dispatch is exactly-once (IdempotencyRepository-backed).
        dup_registration = webhook_service.register_endpoint(tenant_id, webhook_url, ("ptp.created",))
        dup_payload: dict[str, object] = {"tenant_id": str(tenant_id), "ptp_id": "ptp-validation-1"}
        _received.pop("body", None)
        webhook_service.dispatch("ptp.created", dup_payload)
        first_dispatch_delivered = _received.get("body") is not None
        _received.pop("body", None)
        webhook_service.dispatch("ptp.created", dup_payload)  # simulated EventBus redelivery
        second_dispatch_skipped = _received.get("body") is None
        results.append(
            (
                "Part-3: duplicate EventBus dispatch of the same event delivers exactly once",
                first_dispatch_delivered and second_dispatch_skipped,
                f"first_delivered={first_dispatch_delivered} second_skipped={second_dispatch_skipped}",
            )
        )
        webhook_service.deactivate_endpoint(tenant_id, dup_registration.webhook_id)

        # 5f. Part-3: persisted api_rate_limits -- seeded rows readable, unseeded tier is None.
        rate_limit_repo = APIRateLimitRepository(conn)
        growth_limits = rate_limit_repo.get("GROWTH")
        results.append(
            (
                "Part-3: api_rate_limits seed data readable (GROWTH tier)",
                growth_limits is not None
                and growth_limits.requests_per_second == 100
                and growth_limits.burst_capacity == 300,
                f"config={growth_limits}",
            )
        )

        # 6a. Part-3: API key lifecycle service -- issue/rotate/revoke round trip.
        api_key_repo_lifecycle = APIKeyRepository(conn)
        lifecycle = APIKeyLifecycleService(api_key_repo_lifecycle)
        lifecycle_raw_key, lifecycle_record = lifecycle.issue(
            tenant_id, plan_tier="GROWTH", issued_by="validator", expires_at=_NOW + timedelta(days=30)
        )
        rotated_raw_key = lifecycle.rotate(tenant_id, lifecycle_record.api_key_id, "validator")
        results.append(
            (
                "Part-3: APIKeyLifecycleService.rotate() changes the resolvable raw key",
                rotated_raw_key != lifecycle_raw_key
                and APIKeyValidator(repository=api_key_repo_lifecycle).validate(rotated_raw_key).tenant_id
                == str(tenant_id),
                f"rotated={rotated_raw_key != lifecycle_raw_key}",
            )
        )
        lifecycle.revoke(tenant_id, lifecycle_record.api_key_id, "validator")
        try:
            APIKeyValidator(repository=api_key_repo_lifecycle).validate(rotated_raw_key)
            revoke_enforced = False
        except Exception:
            revoke_enforced = True
        results.append(("Part-3: APIKeyLifecycleService.revoke() is enforced by APIKeyValidator", revoke_enforced, ""))

        # 6b. Part-3: API key expiration is enforced (expires_at in the past -> rejected).
        expired_raw_key, _expired_record = lifecycle.issue(
            tenant_id, issued_by="validator", expires_at=_NOW - timedelta(days=1)
        )
        try:
            APIKeyValidator(repository=api_key_repo_lifecycle).validate(expired_raw_key)
            expiration_enforced = False
        except Exception:
            expiration_enforced = True
        results.append(("Part-3: expired API key is rejected", expiration_enforced, ""))

        # 6c. Part-3: api_key_usage -- per-call usage log.
        usage_repo = APIKeyUsageRepository(conn)
        usage_repo.record(tenant_id, lifecycle_record.api_key_id, "/customers/cust-1", 200)
        usage_rows = usage_repo.list_for_tenant(tenant_id)
        results.append(
            (
                "Part-3: api_key_usage records a Public API call",
                len(usage_rows) == 1 and usage_rows[0].route == "/customers/cust-1",
                f"count={len(usage_rows)}",
            )
        )

        # 7. Part-3: PromptVersioning wired into Campaign Management -- activate() denied
        # without a pinned prompt version, permitted once one is pinned.
        campaign_service = CampaignService(campaign_repo, prompt_pins=prompt_repo)
        campaign_service.submit_for_review(tenant_id, campaign_id)
        campaign_service.approve(tenant_id, campaign_id, "validator")
        try:
            campaign_service.activate(tenant_id, campaign_id, "validator", target_call_count=10)
            activation_denied = False
        except CampaignPromptNotPinnedError:
            activation_denied = True
        results.append(("Part-3: campaign activation denied without a pinned prompt version", activation_denied, ""))
        prompt_repo.pin(tenant_id, campaign_id, version.prompt_version_id)
        activated_campaign = campaign_service.activate(tenant_id, campaign_id, "validator", target_call_count=10)
        results.append(
            (
                "Part-3: campaign activation permitted once a prompt version is pinned",
                activated_campaign.status.value == "ACTIVE",
                f"status={activated_campaign.status.value}",
            )
        )

        # 7b. Part-3: admin_audit_views -- Admin-Portal-scoped read-only VIEW.
        admin_audit_view_repo = AdminAuditViewRepository(conn)
        admin_view_rows = admin_audit_view_repo.list_for_tenant(tenant_id)
        results.append(
            (
                "Part-3: admin_audit_views VIEW is queryable (0+ rows, no error)",
                isinstance(admin_view_rows, tuple),
                f"count={len(admin_view_rows)}",
            )
        )

        # 8. Public API: real Postgres-backed API key -> valid key resolves, invalid rejected.
        api_key_repo = APIKeyRepository(conn)
        raw_key = f"sprint025-validation-key-{uuid.uuid4().hex}"
        api_key_repo.create(
            PersistedAPIKeyRecord(
                api_key_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                key_hash=APIKeyValidator.hash_key(raw_key),
                role="",
                created_at=_NOW,
            )
        )
        validator = APIKeyValidator(repository=api_key_repo)
        auth_context = validator.validate(raw_key)
        results.append(
            (
                "Public API: Postgres-backed API key resolves to correct tenant_id",
                auth_context.tenant_id == str(tenant_id),
                f"tenant_id={auth_context.tenant_id}",
            )
        )
        try:
            validator.validate("not-a-real-key")
            invalid_rejected = False
        except Exception:
            invalid_rejected = True
        results.append(("Public API: invalid API key rejected", invalid_rejected, ""))

    finally:
        server.shutdown()
        conn.rollback()
        cur = conn.cursor()
        cur.execute("DELETE FROM api_keys WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM webhook_deliveries WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM webhook_registrations WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM model_configs WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM campaign_prompt_pins WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM prompt_versions WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM sso_config WHERE tenant_id = %s", (str(tenant_id),))
        cur.execute("DELETE FROM campaigns WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
        conn.commit()
        conn.close()

    print(f"{'CHECK':<70} {'RESULT':<8} DETAIL")
    print("-" * 120)
    all_ok = True
    for name, ok, detail in results:
        status_str = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        print(f"{name:<70} {status_str:<8} {detail}")

    return 0 if all_ok else 1


class _HttpxLikeClient:
    """Minimal stdlib-only HTTP POST client satisfying ``HTTPClientPort`` (no new dependency)."""

    def post(self, url: str, *, content: bytes, headers: dict[str, str], timeout: float = 10.0) -> _Response:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(url, data=content, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return _Response(resp.status)
        except urllib.error.HTTPError as exc:
            return _Response(exc.code)
        except OSError as exc:
            raise ConnectionError(str(exc)) from exc


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


if __name__ == "__main__":
    sys.exit(main())
