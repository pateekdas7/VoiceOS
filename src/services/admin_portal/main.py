"""Production entrypoint for the Admin Portal (V5 Ch13).

Serves on ADMIN_PORTAL_PORT (default 8002).
Auth: X-API-Key header (SHA-256 hashed, stored in api_keys table).
      Bearer JWT also accepted if ADMIN_JWT_PUBLIC_KEY_PATH is set.

Required env: POSTGRES_DSN
Optional env: ADMIN_JWT_PUBLIC_KEY_PATH, ADMIN_PORTAL_PORT
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "/opt/voiceos/app")


def create_app():
    import psycopg2

    from src.libs.audit.logger import AuditLogger
    from src.libs.audit.search import AuditSearch
    from src.libs.repositories.admin_audit_view import AdminAuditViewRepository
    from src.libs.repositories.ai_config import ModelConfigRepository, PromptVersionRepository
    from src.libs.repositories.audit import AuditRepository
    from src.libs.repositories.billing import BillingRepository, InvoiceRepository, UsageRepository
    from src.libs.repositories.campaign import CampaignRepository
    from src.libs.repositories.integration import APIKeyRepository
    from src.libs.repositories.invitation import InvitationRepository
    from src.libs.repositories.tenant import TenantRepository
    from src.libs.repositories.user import UserRepository
    from src.services.admin_portal import create_admin_api
    from src.services.admin_portal.ai_config_admin import AIConfigAdminController
    from src.services.admin_portal.api_key_admin import APIKeyAdminController
    from src.services.admin_portal.audit_admin import AuditAdminController
    from src.services.admin_portal.billing_admin import BillingAdminController
    from src.services.admin_portal.campaign_admin import CampaignAdminController
    from src.services.admin_portal.tenant_admin import TenantAdminController
    from src.services.admin_portal.user_admin import UserAdminController
    from src.services.ai_config.model_config import ModelConfigService
    from src.services.ai_config.prompt_versioning import PromptVersioningService
    from src.services.ai_config.service import AIConfigService
    from src.services.api_platform.api_key_lifecycle import APIKeyLifecycleService
    from src.services.auth.api_key_validator import APIKeyValidator
    from src.services.auth.jwt_validator import JWTValidator
    from src.services.auth.service import AuthService
    from src.services.billing.entitlement import EntitlementEngine
    from src.services.billing.invoice import InvoiceGenerator
    from src.services.billing.service import BillingService
    from src.services.billing.subscription import SubscriptionManager
    from src.services.campaign_management.service import CampaignService
    from src.services.metering.aggregator import UsageAggregator
    from src.services.policy_engine.engine import PolicyEngine
    from src.services.policy_engine.service import PolicyEngineService
    from src.services.reporting.exporter import ExportService
    from src.services.tenant_management.service import TenantService
    from src.services.user_management.invitation import InvitationService
    from src.services.user_management.service import UserService
    from src.services.user_management.sso_stub import SSOIntegration

    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        raise RuntimeError("POSTGRES_DSN is required")
    conn = psycopg2.connect(dsn)

    # ── Auth ──────────────────────────────────────────────────────────────────
    api_key_repo = APIKeyRepository(conn)
    api_key_validator = APIKeyValidator(api_key_repo)

    jwt_validator = None
    pub_key_path = os.environ.get("ADMIN_JWT_PUBLIC_KEY_PATH")
    if pub_key_path and os.path.exists(pub_key_path):
        from cryptography.hazmat.primitives import serialization
        with open(pub_key_path, "rb") as fh:
            pub_key = serialization.load_pem_public_key(fh.read())
        jwt_validator = JWTValidator(pub_key)

    auth_service = AuthService(
        jwt_validator=jwt_validator,
        api_key_validator=api_key_validator,
    )

    # ── Audit ─────────────────────────────────────────────────────────────────
    audit_repo = AuditRepository(conn)
    audit_logger = AuditLogger(audit_repo)
    audit_search = AuditSearch(audit_repo)
    admin_audit_repo = AdminAuditViewRepository(conn)

    # ── Tenant ────────────────────────────────────────────────────────────────
    tenant_service = TenantService(TenantRepository(conn), audit_logger=audit_logger)
    tenant_admin = TenantAdminController(tenant_service)

    # ── Users ─────────────────────────────────────────────────────────────────
    user_repo = UserRepository(conn)
    invitation_service = InvitationService(InvitationRepository(conn), user_repo, audit_logger)
    user_service = UserService(user_repo, invitation_service)
    user_admin = UserAdminController(user_service, SSOIntegration(conn))

    # ── Campaigns ─────────────────────────────────────────────────────────────
    campaign_service = CampaignService(CampaignRepository(conn))
    campaign_admin = CampaignAdminController(campaign_service, PolicyEngineService(PolicyEngine()))

    # ── Billing ───────────────────────────────────────────────────────────────
    class _AlwaysEntitledPolicy:
        def check(self, *a, **kw):
            from src.libs.contracts.models.billing import EntitlementResult
            return EntitlementResult(allowed=True)

    invoice_repo = InvoiceRepository(conn)
    usage_repo = UsageRepository(conn)
    billing_service = BillingService(
        SubscriptionManager(BillingRepository(conn)),
        EntitlementEngine(_AlwaysEntitledPolicy()),
        InvoiceGenerator(usage_repo, invoice_repo),
    )
    billing_admin = BillingAdminController(
        billing_service,
        invoice_repo,
        UsageAggregator(usage_repo),
    )

    # ── Audit Admin ───────────────────────────────────────────────────────────
    audit_admin = AuditAdminController(
        audit_search,
        ExportService(),
        admin_audit_repo,
    )

    # ── AI Config ─────────────────────────────────────────────────────────────
    ai_config_service = AIConfigService(
        PromptVersioningService(PromptVersionRepository(conn)),
        ModelConfigService(ModelConfigRepository(conn)),
    )
    ai_config_admin = AIConfigAdminController(ai_config_service)

    # ── API Key Admin ─────────────────────────────────────────────────────────
    api_key_admin = APIKeyAdminController(APIKeyLifecycleService(api_key_repo, audit_logger))

    return create_admin_api(
        auth_service=auth_service,
        tenant_admin=tenant_admin,
        user_admin=user_admin,
        campaign_admin=campaign_admin,
        billing_admin=billing_admin,
        audit_admin=audit_admin,
        ai_config_admin=ai_config_admin,
        api_key_admin=api_key_admin,
        audit_logger=audit_logger,
    )
