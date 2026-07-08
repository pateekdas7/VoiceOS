"""Unit tests for Sprint-002 persistent data models.

Verifies: instantiation with valid fields, enum values, field constraints,
immutability (frozen=True), and discriminator field values.

Architecture: V6 Ch4 (Testing Standards); Sprint-002 AC-4.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from src.libs.contracts.context import ConsentStatus
from src.libs.contracts.models import (
    # campaign
    ABTestVariant,
    # customer
    Address,
    AudienceCriteria,
    # billing
    BillingSubscription,
    # tenant
    Branch,
    BusinessUnit,
    # collections
    CallbackRequest,
    Campaign,
    CampaignStatus,
    # consent
    Consent,
    ConsentRecord,
    ConsentType,
    Customer,
    CustomerContact,
    # loan
    DPDRecord,
    EMIEntry,
    EMISchedule,
    EMIStatus,
    EscalationRecord,
    Invoice,
    InvoiceStatus,
    IsolationProfile,
    LoanAccount,
    LoanOutstanding,
    LoanStatus,
    Organization,
    # user
    OrgScope,
    Party,
    PartyRole,
    PromiseToPay,
    PTPStatus,
    RetryPolicy,
    Role,
    RoleAssignment,
    Settlement,
    SettlementStatus,
    SubscriptionTier,
    Tenant,
    TenantStatus,
    UsageEvent,
    UsageType,
    User,
)
from src.libs.contracts.primitives import CallId, CampaignId, CustomerId, TenantId

TENANT = TenantId(str(uuid.uuid4()))
CUSTOMER = CustomerId(str(uuid.uuid4()))
CALL = CallId(str(uuid.uuid4()))
CAMPAIGN = CampaignId(str(uuid.uuid4()))
NOW = datetime(2026, 6, 30, 12, 0, 0, tzinfo=UTC)
TODAY = date(2026, 6, 30)


# ---------------------------------------------------------------------------
# Customer models
# ---------------------------------------------------------------------------


class TestAddress:
    def test_instantiation(self) -> None:
        addr = Address(
            line1="42 Main Road",
            city="Mumbai",
            state="Maharashtra",
            pincode="400001",
        )
        assert addr.country == "IN"

    def test_frozen(self) -> None:
        addr = Address(line1="1 Test St", city="Delhi", state="Delhi", pincode="110001")
        with pytest.raises(ValidationError):
            addr.city = "Mumbai"  # type: ignore[misc]


class TestCustomerContact:
    def test_mobile_contact(self) -> None:
        c = CustomerContact(contact_type="MOBILE", value="+919876543210")
        assert c.is_primary is False
        assert c.is_dnc is False
        assert c.consent_captured is False

    def test_primary_contact(self) -> None:
        c = CustomerContact(contact_type="MOBILE", value="+919999", is_primary=True)
        assert c.is_primary is True


class TestParty:
    def test_instantiation(self) -> None:
        p = Party(
            party_id="p-001",
            customer_id=CUSTOMER,
            role=PartyRole.GUARANTOR,
            name="Suresh Kumar",
        )
        assert p.role == "GUARANTOR"
        assert p.contacts == ()

    def test_party_role_values(self) -> None:
        assert PartyRole.PRIMARY_BORROWER == "PRIMARY_BORROWER"
        assert PartyRole.CO_BORROWER == "CO_BORROWER"
        assert PartyRole.GUARANTOR == "GUARANTOR"
        assert PartyRole.NOMINEE == "NOMINEE"


class TestCustomer:
    def test_instantiation(self) -> None:
        cust = Customer(
            customer_id=CUSTOMER,
            tenant_id=TENANT,
            crm_id="CRM-12345",
            name="Ravi Kumar",
            preferred_language="hi",
            created_at=NOW,
            updated_at=NOW,
        )
        assert cust.is_active is True
        assert cust.data_erasure_requested is False

    def test_frozen(self) -> None:
        cust = Customer(
            customer_id=CUSTOMER,
            tenant_id=TENANT,
            crm_id="CRM-1",
            name="Test",
            created_at=NOW,
            updated_at=NOW,
        )
        with pytest.raises(ValidationError):
            cust.name = "Changed"  # type: ignore[misc]

    def test_contacts_default_empty(self) -> None:
        cust = Customer(
            customer_id=CUSTOMER,
            tenant_id=TENANT,
            crm_id="CRM-2",
            name="Test",
            created_at=NOW,
            updated_at=NOW,
        )
        assert cust.contacts == ()


# ---------------------------------------------------------------------------
# Loan models
# ---------------------------------------------------------------------------


class TestEMIEntry:
    def test_instantiation(self) -> None:
        emi = EMIEntry(
            instalment_number=1,
            due_date=TODAY,
            principal_minor=50000,
            interest_minor=5000,
            total_minor=55000,
        )
        assert emi.status == EMIStatus.PENDING
        assert emi.paid_minor == 0

    def test_instalment_number_below_1_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EMIEntry(
                instalment_number=0,
                due_date=TODAY,
                principal_minor=50000,
                interest_minor=5000,
                total_minor=55000,
            )


class TestEMIStatus:
    def test_values(self) -> None:
        assert EMIStatus.PENDING == "PENDING"
        assert EMIStatus.PAID == "PAID"
        assert EMIStatus.OVERDUE == "OVERDUE"


class TestEMISchedule:
    def test_instantiation(self) -> None:
        sched = EMISchedule(loan_account_id="LA-001", currency="INR")
        assert sched.instalments == ()


class TestDPDRecord:
    def test_instantiation(self) -> None:
        rec = DPDRecord(
            loan_account_id="LA-001",
            dpd=30,
            as_of_date=TODAY,
            overdue_minor=300000,
            currency="INR",
        )
        assert rec.dpd == 30


class TestLoanOutstanding:
    def test_instantiation(self) -> None:
        outstanding = LoanOutstanding(
            loan_account_id="LA-001",
            principal_minor=500000,
            interest_minor=50000,
            penalty_minor=5000,
            total_minor=555000,
            currency="INR",
            as_of=NOW,
        )
        assert outstanding.total_minor == 555000

    def test_negative_principal_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoanOutstanding(
                loan_account_id="LA-001",
                principal_minor=-1,
                interest_minor=0,
                total_minor=0,
                currency="INR",
                as_of=NOW,
            )


class TestLoanStatus:
    def test_values(self) -> None:
        assert LoanStatus.ACTIVE == "ACTIVE"
        assert LoanStatus.DELINQUENT == "DELINQUENT"
        assert LoanStatus.NPA == "NPA"
        assert LoanStatus.SETTLED == "SETTLED"
        assert LoanStatus.WRITTEN_OFF == "WRITTEN_OFF"
        assert LoanStatus.CLOSED == "CLOSED"


class TestLoanAccount:
    def test_instantiation(self) -> None:
        loan = LoanAccount(
            loan_account_id="LA-9999",
            tenant_id=TENANT,
            customer_id=CUSTOMER,
            product_type="PERSONAL_LOAN",
            disbursed_amount_minor=1000000,
            currency="INR",
            interest_rate_bps=1200,
            tenure_months=24,
            disbursement_date=date(2024, 1, 1),
            maturity_date=date(2026, 1, 1),
            created_at=NOW,
            updated_at=NOW,
        )
        assert loan.status == LoanStatus.ACTIVE
        assert loan.dpd == 0

    def test_tenure_below_1_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoanAccount(
                loan_account_id="LA-X",
                tenant_id=TENANT,
                customer_id=CUSTOMER,
                product_type="PERSONAL_LOAN",
                disbursed_amount_minor=100000,
                currency="INR",
                interest_rate_bps=1200,
                tenure_months=0,
                disbursement_date=TODAY,
                maturity_date=TODAY,
                created_at=NOW,
                updated_at=NOW,
            )


# ---------------------------------------------------------------------------
# Collections models
# ---------------------------------------------------------------------------


class TestPTPStatus:
    def test_values(self) -> None:
        assert PTPStatus.PENDING == "PENDING"
        assert PTPStatus.KEPT == "KEPT"
        assert PTPStatus.BROKEN == "BROKEN"


class TestPromiseToPay:
    def test_instantiation(self) -> None:
        ptp = PromiseToPay(
            ptp_id=str(uuid.uuid4()),
            tenant_id=TENANT,
            call_id=CALL,
            customer_id=CUSTOMER,
            loan_account_id="LA-9999",
            promised_amount_minor=500000,
            currency="INR",
            promise_date=datetime(2026, 7, 10, tzinfo=UTC),
            recorded_at=NOW,
            updated_at=NOW,
        )
        assert ptp.status == PTPStatus.PENDING
        assert ptp.notes == ""


class TestSettlement:
    def test_instantiation(self) -> None:
        sett = Settlement(
            settlement_id=str(uuid.uuid4()),
            tenant_id=TENANT,
            customer_id=CUSTOMER,
            loan_account_id="LA-9999",
            settlement_amount_minor=800000,
            currency="INR",
            offer_expiry=datetime(2026, 7, 15, tzinfo=UTC),
            proposed_at=NOW,
            updated_at=NOW,
        )
        assert sett.status == SettlementStatus.PROPOSED
        assert sett.waiver_amount_minor == 0


class TestCallbackRequest:
    def test_instantiation(self) -> None:
        cb = CallbackRequest(
            callback_id=str(uuid.uuid4()),
            tenant_id=TENANT,
            call_id=CALL,
            customer_id=CUSTOMER,
            loan_account_id="LA-9999",
            preferred_time=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
            phone_number="+919876543210",
            recorded_at=NOW,
        )
        assert cb.is_fulfilled is False
        assert cb.timezone == "Asia/Kolkata"


class TestEscalationRecord:
    def test_instantiation(self) -> None:
        esc = EscalationRecord(
            escalation_id=str(uuid.uuid4()),
            tenant_id=TENANT,
            call_id=CALL,
            customer_id=CUSTOMER,
            reason="LEGAL_THREAT",
            escalated_at=NOW,
        )
        assert esc.resolved_at is None
        assert esc.escalated_to == ""


# ---------------------------------------------------------------------------
# Consent models
# ---------------------------------------------------------------------------


class TestConsentType:
    def test_values(self) -> None:
        assert ConsentType.CONTACT == "CONTACT"
        assert ConsentType.VOICE_RECORDING == "VOICE_RECORDING"
        assert ConsentType.WHATSAPP == "WHATSAPP"


class TestConsentRecord:
    def test_instantiation(self) -> None:
        rec = ConsentRecord(
            record_id=str(uuid.uuid4()),
            consent_id=str(uuid.uuid4()),
            action="GRANTED",
            actor_id="ivr-system",
            channel="ivr",
            recorded_at=NOW,
        )
        assert rec.ip_address == ""


class TestConsent:
    def test_instantiation(self) -> None:
        c = Consent(
            consent_id=str(uuid.uuid4()),
            tenant_id=TENANT,
            customer_id=CUSTOMER,
            consent_type=ConsentType.CONTACT,
            status=ConsentStatus.GRANTED,
            granted_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
        assert c.status == "GRANTED"
        assert c.revoked_at is None


# ---------------------------------------------------------------------------
# Campaign models
# ---------------------------------------------------------------------------


class TestRetryPolicy:
    def test_defaults(self) -> None:
        rp = RetryPolicy()
        assert rp.max_attempts == 3
        assert rp.retry_interval_hours == 24

    def test_max_attempts_above_limit_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RetryPolicy(max_attempts=11)


class TestAudienceCriteria:
    def test_defaults(self) -> None:
        ac = AudienceCriteria()
        assert ac.min_dpd == 0
        assert ac.exclude_ptp_active is True
        assert ac.exclude_dnc is True


class TestABTestVariant:
    def test_instantiation(self) -> None:
        v = ABTestVariant(variant_id="v-1", name="control", traffic_weight=50)
        assert v.strategy_override == ""

    def test_weight_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ABTestVariant(variant_id="v-1", name="x", traffic_weight=101)


class TestCampaignStatus:
    def test_values(self) -> None:
        assert CampaignStatus.DRAFT == "DRAFT"
        assert CampaignStatus.ACTIVE == "ACTIVE"
        assert CampaignStatus.COMPLETED == "COMPLETED"


class TestCampaign:
    def test_instantiation(self) -> None:
        camp = Campaign(
            campaign_id=CAMPAIGN,
            tenant_id=TENANT,
            name="June Collections",
            status=CampaignStatus.DRAFT,
            audience_criteria=AudienceCriteria(min_dpd=30),
            retry_policy=RetryPolicy(),
            created_at=NOW,
            updated_at=NOW,
            created_by="admin-1",
        )
        assert camp.target_call_count == 0
        assert camp.ab_variants == ()


# ---------------------------------------------------------------------------
# Tenant models
# ---------------------------------------------------------------------------


class TestTenantStatus:
    def test_values(self) -> None:
        assert TenantStatus.TRIAL == "TRIAL"
        assert TenantStatus.SANDBOX == "SANDBOX"
        assert TenantStatus.PRODUCTION == "PRODUCTION"
        assert TenantStatus.SUSPENDED == "SUSPENDED"
        assert TenantStatus.CANCELLED == "CANCELLED"
        assert TenantStatus.DELETING == "DELETING"
        assert TenantStatus.DELETED == "DELETED"


class TestIsolationProfile:
    def test_values(self) -> None:
        assert IsolationProfile.SHARED == "SHARED"
        assert IsolationProfile.DEDICATED_SCHEMA == "DEDICATED_SCHEMA"
        assert IsolationProfile.DEDICATED_CLUSTER == "DEDICATED_CLUSTER"


class TestBranch:
    def test_instantiation(self) -> None:
        b = Branch(branch_id="br-001", name="Mumbai South Branch")
        assert b.is_active is True


class TestBusinessUnit:
    def test_instantiation(self) -> None:
        bu = BusinessUnit(bu_id="bu-001", name="Retail Collections")
        assert bu.branches == ()


class TestOrganization:
    def test_instantiation(self) -> None:
        org = Organization(
            org_id=str(uuid.uuid4()),
            tenant_id=TENANT,
            legal_name="Acme Collections Pvt Ltd",
            created_at=NOW,
            updated_at=NOW,
        )
        assert org.country == "IN"
        assert org.business_units == ()


class TestTenant:
    def test_instantiation(self) -> None:
        t = Tenant(
            tenant_id=TENANT,
            slug="acme-collections",
            display_name="Acme Collections",
            subscription_tier="ENTERPRISE",
            created_at=NOW,
            updated_at=NOW,
        )
        assert t.status == TenantStatus.TRIAL
        assert t.isolation_profile == IsolationProfile.SHARED

    def test_invalid_slug_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Tenant(
                tenant_id=TENANT,
                slug="UPPERCASE_SLUG",
                display_name="Test",
                subscription_tier="STARTER",
                created_at=NOW,
                updated_at=NOW,
            )


# ---------------------------------------------------------------------------
# User / RBAC models
# ---------------------------------------------------------------------------


class TestOrgScope:
    def test_instantiation(self) -> None:
        scope = OrgScope(scope_type="TENANT", scope_id=TENANT)
        assert scope.scope_type == "TENANT"


class TestRole:
    def test_instantiation(self) -> None:
        role = Role(
            role_id=str(uuid.uuid4()),
            tenant_id=TENANT,
            name="campaign-manager",
            permissions=("campaign:create", "campaign:view"),
            created_at=NOW,
            updated_at=NOW,
        )
        assert role.is_system_role is False
        assert "campaign:create" in role.permissions


class TestRoleAssignment:
    def test_instantiation(self) -> None:
        ra = RoleAssignment(
            assignment_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            role_id=str(uuid.uuid4()),
            org_scope=OrgScope(scope_type="TENANT", scope_id=TENANT),
            assigned_by=str(uuid.uuid4()),
            assigned_at=NOW,
        )
        assert ra.expires_at is None


class TestUser:
    def test_instantiation(self) -> None:
        user = User(
            user_id=str(uuid.uuid4()),
            tenant_id=TENANT,
            email="admin@acme.com",
            name="Admin User",
            created_at=NOW,
            updated_at=NOW,
        )
        assert user.is_active is True
        assert user.is_service_account is False
        assert user.mfa_enabled is False
        assert user.role_assignments == ()


# ---------------------------------------------------------------------------
# Billing models
# ---------------------------------------------------------------------------


class TestUsageType:
    def test_values(self) -> None:
        assert UsageType.CALL_MINUTE == "CALL_MINUTE"
        assert UsageType.SMS_MESSAGE == "SMS_MESSAGE"
        assert UsageType.AI_TOKEN == "AI_TOKEN"


class TestInvoiceStatus:
    def test_values(self) -> None:
        assert InvoiceStatus.DRAFT == "DRAFT"
        assert InvoiceStatus.PAID == "PAID"
        assert InvoiceStatus.VOID == "VOID"


class TestSubscriptionTier:
    def test_values(self) -> None:
        assert SubscriptionTier.STARTER == "STARTER"
        assert SubscriptionTier.ENTERPRISE == "ENTERPRISE"


class TestUsageEvent:
    def test_instantiation(self) -> None:
        ue = UsageEvent(
            usage_event_id=str(uuid.uuid4()),
            tenant_id=TENANT,
            usage_type=UsageType.CALL_MINUTE,
            quantity=3,
            unit_cost_minor=100,
            total_cost_minor=300,
            currency="INR",
            occurred_at_bucket="2026-06-30T14:00:00Z",
        )
        assert ue.resource_id == ""
        assert ue.invoice_id == ""

    def test_negative_quantity_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UsageEvent(
                usage_event_id=str(uuid.uuid4()),
                tenant_id=TENANT,
                usage_type=UsageType.CALL_MINUTE,
                quantity=-1,
                unit_cost_minor=100,
                total_cost_minor=0,
                currency="INR",
                occurred_at_bucket="2026-06-30T14:00:00Z",
            )


class TestInvoice:
    def test_instantiation(self) -> None:
        inv = Invoice(
            invoice_id="INV-2026-06",
            tenant_id=TENANT,
            billing_period_start=datetime(2026, 6, 1, tzinfo=UTC),
            billing_period_end=datetime(2026, 6, 30, tzinfo=UTC),
            subtotal_minor=4900000,
            tax_minor=882000,
            total_minor=5782000,
            currency="INR",
            created_at=NOW,
            updated_at=NOW,
        )
        assert inv.status == InvoiceStatus.DRAFT
        assert inv.issued_at is None
        assert inv.paid_at is None


class TestBillingSubscription:
    def test_instantiation(self) -> None:
        sub = BillingSubscription(
            subscription_id=str(uuid.uuid4()),
            tenant_id=TENANT,
            tier=SubscriptionTier.ENTERPRISE,
            rate_card_version="v2.1",
            contract_start=NOW,
            base_fee_minor=500000,
            created_at=NOW,
            updated_at=NOW,
        )
        assert sub.is_active is True
        assert sub.contract_end is None
        assert sub.currency == "INR"
