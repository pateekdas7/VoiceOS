"""Repository (DAO) layer for VoiceOS authoritative Postgres domains.

Every repository is tenant-scoped (every query includes ``tenant_id`` in the
WHERE clause — enforced mechanically by ``BaseRepository``) and speaks raw
SQL through a psycopg2-compatible connection, matching the RelationshipMemoryStore
(Sprint-010) precedent rather than an ORM — business logic stays independent
of any specific persistence framework (CLAUDE.md AI Model Rules extend to the
data layer: repositories are the only code that knows SQL).

Architecture: V3 Ch5 (Persistent Storage); V6 Ch7 (Data Modeling Standards).
"""

from __future__ import annotations

from .analytics import AnalyticsDailyRepository
from .audit import AuditRepository, ImmutableAuditLogError
from .base import BaseRepository
from .bi import BIRepository
from .billing import BillingRepository, InvoiceRepository, UsageRepository
from .call_disposition import CallDispositionRepository
from .callback import CallbackRepository
from .campaign import CampaignRepository
from .consent import ConsentRepository
from .customer import CustomerRepository
from .emi_schedule import EMIScheduleRepository
from .escalation import EscalationRepository
from .idempotency import IdempotencyRepository
from .invitation import Invitation, InvitationRepository
from .loan_account import LoanAccountRepository
from .organization import OrganizationRepository
from .party import PartyRepository
from .promise_to_pay import PromiseToPayRepository
from .saas_ops import FeatureFlagRepository, RolloutRepository, TenantMigrationRepository
from .settlement import SettlementRepository
from .tenant import TenantRepository
from .user import UserRepository

__all__ = [
    "AnalyticsDailyRepository",
    "AuditRepository",
    "BIRepository",
    "BaseRepository",
    "BillingRepository",
    "CallDispositionRepository",
    "CallbackRepository",
    "CampaignRepository",
    "ConsentRepository",
    "CustomerRepository",
    "EMIScheduleRepository",
    "EscalationRepository",
    "FeatureFlagRepository",
    "IdempotencyRepository",
    "ImmutableAuditLogError",
    "Invitation",
    "InvitationRepository",
    "InvoiceRepository",
    "LoanAccountRepository",
    "OrganizationRepository",
    "PartyRepository",
    "PromiseToPayRepository",
    "RolloutRepository",
    "SettlementRepository",
    "TenantMigrationRepository",
    "TenantRepository",
    "UsageRepository",
    "UserRepository",
]
