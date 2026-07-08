"""Unit tests for Sprint-002 domain event contracts.

Verifies: instantiation with valid fields, event_type discriminator value,
JSON serialisation round-trip, immutability (frozen=True), and field
validation constraints.

Architecture: V6 Ch4 (Testing Standards); Sprint-002 AC-3.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.libs.contracts.events import (
    # audio
    AudioFrameReceived,
    AudioSessionEnded,
    AudioSessionStarted,
    # compliance
    AuditEventEmitted,
    BargeinDetected,
    # saas
    BillingInvoiceGenerated,
    CallDispositioned,
    CampaignStarted,
    # reliability
    CircuitBreakerClosed,
    CircuitBreakerOpened,
    ConsentRecorded,
    ConsentRevoked,
    CustomerCreated,
    DataErasureRequested,
    # intelligence
    EntityExtracted,
    IdempotencyKeyCreated,
    IntentClassified,
    LoanAccountUpdated,
    NegotiationMoveProposed,
    PIIRedacted,
    # dialogue
    PlaybackCompleted,
    PlaybackFlushed,
    PlaybackStarted,
    PolicyDecisionMade,
    PTPCreated,
    RecoveryCompleted,
    RecoveryStarted,
    ResponsePlanAssembled,
    ResponsePlanCreated,
    RiskFlagRaised,
    SnapshotCreated,
    StrategySelected,
    TenantProvisioned,
    TenantSuspended,
    TurnCompleted,
    TurnStarted,
    UsageEventRecorded,
    VADSpeechEnd,
    VADSpeechStart,
)
from src.libs.contracts.primitives import CallId, CampaignId, CustomerId, TenantId

TENANT = TenantId(str(uuid.uuid4()))
CALL = CallId(str(uuid.uuid4()))
CUSTOMER = CustomerId(str(uuid.uuid4()))
CAMPAIGN = CampaignId(str(uuid.uuid4()))
NOW = datetime(2026, 6, 30, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Audio events
# ---------------------------------------------------------------------------


class TestAudioSessionStarted:
    def test_instantiation(self) -> None:
        evt = AudioSessionStarted(
            tenant_id=TENANT,
            call_id=CALL,
            caller_phone="+911234567890",
            encoding="mulaw",
            sample_rate=8000,
        )
        assert evt.event_type == "audio.session.started"
        assert evt.call_id == CALL

    def test_sample_rate_bounds(self) -> None:
        with pytest.raises(ValidationError):
            AudioSessionStarted(
                tenant_id=TENANT,
                call_id=CALL,
                caller_phone="+911234567890",
                encoding="mulaw",
                sample_rate=4000,  # below 8000
            )

    def test_json_round_trip(self) -> None:
        evt = AudioSessionStarted(
            tenant_id=TENANT,
            call_id=CALL,
            caller_phone="+91999",
            encoding="pcm16le",
            sample_rate=16000,
        )
        restored = AudioSessionStarted.model_validate_json(evt.model_dump_json())
        assert restored.event_type == evt.event_type
        assert restored.call_id == evt.call_id

    def test_frozen(self) -> None:
        evt = AudioSessionStarted(
            tenant_id=TENANT,
            call_id=CALL,
            caller_phone="+91999",
            encoding="mulaw",
            sample_rate=8000,
        )
        with pytest.raises(ValidationError):
            evt.call_id = "new-id"  # type: ignore[misc,assignment]


class TestAudioFrameReceived:
    def test_instantiation(self) -> None:
        evt = AudioFrameReceived(
            tenant_id=TENANT,
            call_id=CALL,
            seq=0,
            rtp_ts=0,
            frame_size_bytes=160,
        )
        assert evt.event_type == "audio.frame.received"

    def test_negative_seq_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AudioFrameReceived(
                tenant_id=TENANT,
                call_id=CALL,
                seq=-1,
                rtp_ts=0,
                frame_size_bytes=160,
            )


class TestBargeinDetected:
    def test_instantiation(self) -> None:
        evt = BargeinDetected(
            tenant_id=TENANT,
            call_id=CALL,
            detected_at_ms=3500,
            playback_seq=2,
        )
        assert evt.event_type == "audio.barge_in.detected"


class TestVADSpeechStart:
    def test_instantiation(self) -> None:
        evt = VADSpeechStart(
            tenant_id=TENANT,
            call_id=CALL,
            start_ms=1000,
            energy_db=-18.5,
        )
        assert evt.event_type == "audio.vad.speech_start"


class TestVADSpeechEnd:
    def test_instantiation(self) -> None:
        evt = VADSpeechEnd(
            tenant_id=TENANT,
            call_id=CALL,
            start_ms=1000,
            end_ms=2500,
            duration_ms=1500,
        )
        assert evt.event_type == "audio.vad.speech_end"


class TestAudioSessionEnded:
    def test_instantiation(self) -> None:
        evt = AudioSessionEnded(
            tenant_id=TENANT,
            call_id=CALL,
            reason="customer_hangup",
            duration_ms=45000,
        )
        assert evt.event_type == "audio.session.ended"


# ---------------------------------------------------------------------------
# Dialogue events
# ---------------------------------------------------------------------------


class TestTurnStarted:
    def test_instantiation(self) -> None:
        evt = TurnStarted(
            tenant_id=TENANT,
            call_id=CALL,
            turn_id="t-1",
            turn_index=0,
            role="customer",
        )
        assert evt.event_type == "dialogue.turn.started"

    def test_negative_turn_index_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TurnStarted(
                tenant_id=TENANT,
                call_id=CALL,
                turn_id="t-1",
                turn_index=-1,
                role="customer",
            )


class TestTurnCompleted:
    def test_instantiation(self) -> None:
        evt = TurnCompleted(
            tenant_id=TENANT,
            call_id=CALL,
            turn_id="t-1",
            transcript="I can pay next week",
            duration_ms=2100,
            word_count=5,
        )
        assert evt.event_type == "dialogue.turn.completed"


class TestResponsePlanCreated:
    def test_instantiation(self) -> None:
        evt = ResponsePlanCreated(
            tenant_id=TENANT,
            call_id=CALL,
            turn_id="t-1",
            plan_id="p-1",
            plan_version=1,
            strategy="NEGOTIATE",
        )
        assert evt.event_type == "dialogue.response_plan.created"
        assert evt.plan_version == 1

    def test_version_below_1_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ResponsePlanCreated(
                tenant_id=TENANT,
                call_id=CALL,
                turn_id="t-1",
                plan_id="p-1",
                plan_version=0,
                strategy="NEGOTIATE",
            )


class TestPlaybackStarted:
    def test_instantiation(self) -> None:
        evt = PlaybackStarted(
            tenant_id=TENANT,
            call_id=CALL,
            plan_id="p-1",
            clause_count=3,
            voice_id="veena-v2",
        )
        assert evt.event_type == "dialogue.playback.started"


class TestPlaybackCompleted:
    def test_instantiation(self) -> None:
        evt = PlaybackCompleted(
            tenant_id=TENANT,
            call_id=CALL,
            plan_id="p-1",
            played_ms=4200,
        )
        assert evt.event_type == "dialogue.playback.completed"


class TestPlaybackFlushed:
    def test_instantiation(self) -> None:
        evt = PlaybackFlushed(
            tenant_id=TENANT,
            call_id=CALL,
            plan_id="p-1",
            reason="barge_in",
            clauses_played=1,
        )
        assert evt.event_type == "dialogue.playback.flushed"


# ---------------------------------------------------------------------------
# Intelligence events
# ---------------------------------------------------------------------------


class TestIntentClassified:
    def test_instantiation(self) -> None:
        evt = IntentClassified(
            tenant_id=TENANT,
            call_id=CALL,
            turn_id="t-1",
            label="PAYMENT",
            confidence=0.92,
        )
        assert evt.event_type == "intelligence.intent.classified"

    def test_confidence_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            IntentClassified(
                tenant_id=TENANT,
                call_id=CALL,
                turn_id="t-1",
                label="PAYMENT",
                confidence=1.1,
            )


class TestEntityExtracted:
    def test_instantiation(self) -> None:
        evt = EntityExtracted(
            tenant_id=TENANT,
            call_id=CALL,
            turn_id="t-1",
            entity_key="promised_amount",
            entity_value="5000",
            confidence=0.88,
        )
        assert evt.event_type == "intelligence.entity.extracted"


class TestRiskFlagRaised:
    def test_instantiation(self) -> None:
        evt = RiskFlagRaised(
            tenant_id=TENANT,
            call_id=CALL,
            flag_id="ABUSE_DETECTED",
            level="HIGH",
            description="Customer used threatening language.",
        )
        assert evt.event_type == "intelligence.risk.flag_raised"


class TestStrategySelected:
    def test_instantiation(self) -> None:
        evt = StrategySelected(
            tenant_id=TENANT,
            call_id=CALL,
            turn_id="t-1",
            action="NEGOTIATE",
            rationale="Customer expressed willingness to pay.",
            confidence=0.85,
        )
        assert evt.event_type == "intelligence.strategy.selected"


class TestNegotiationMoveProposed:
    def test_instantiation(self) -> None:
        evt = NegotiationMoveProposed(
            tenant_id=TENANT,
            call_id=CALL,
            turn_id="t-1",
            move_type="PARTIAL_PAYMENT",
            proposed_amount_minor=500000,
            currency="INR",
            concession_count=0,
        )
        assert evt.event_type == "intelligence.negotiation.move_proposed"

    def test_negative_amount_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NegotiationMoveProposed(
                tenant_id=TENANT,
                call_id=CALL,
                turn_id="t-1",
                move_type="PARTIAL_PAYMENT",
                proposed_amount_minor=-1,
                currency="INR",
                concession_count=0,
            )


class TestResponsePlanAssembled:
    def test_instantiation(self) -> None:
        evt = ResponsePlanAssembled(
            tenant_id=TENANT,
            call_id=CALL,
            turn_id="t-1",
            plan_id="p-1",
            plan_version=1,
            engine_latency_ms=120,
        )
        assert evt.event_type == "intelligence.response_plan.assembled"


# ---------------------------------------------------------------------------
# Reliability events
# ---------------------------------------------------------------------------


class TestSnapshotCreated:
    def test_instantiation(self) -> None:
        evt = SnapshotCreated(
            tenant_id=TENANT,
            call_id=CALL,
            snapshot_key=f"snap:{TENANT}:{CALL}:v1",
            snapshot_version=1,
            size_bytes=4096,
        )
        assert evt.event_type == "reliability.snapshot.created"

    def test_version_below_1_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SnapshotCreated(
                tenant_id=TENANT,
                call_id=CALL,
                snapshot_key="k",
                snapshot_version=0,
                size_bytes=0,
            )


class TestRecoveryStarted:
    def test_instantiation(self) -> None:
        evt = RecoveryStarted(
            tenant_id=TENANT,
            call_id=CALL,
            last_snapshot_version=3,
            triggered_by="process_restart",
            events_to_replay=7,
        )
        assert evt.event_type == "reliability.recovery.started"


class TestRecoveryCompleted:
    def test_instantiation(self) -> None:
        evt = RecoveryCompleted(
            tenant_id=TENANT,
            call_id=CALL,
            recovered_to_version=10,
            duration_ms=230,
            events_replayed=7,
        )
        assert evt.event_type == "reliability.recovery.completed"


class TestIdempotencyKeyCreated:
    def test_instantiation(self) -> None:
        evt = IdempotencyKeyCreated(
            tenant_id=TENANT,
            key="idem-abc-123",
            resource_type="ptp",
            expires_at=NOW,
        )
        assert evt.event_type == "reliability.idempotency.key_created"


class TestCircuitBreakerOpened:
    def test_instantiation(self) -> None:
        evt = CircuitBreakerOpened(
            tenant_id=TENANT,
            service_name="llm-adapter",
            failure_count=5,
            threshold=5,
            window_ms=60000,
        )
        assert evt.event_type == "reliability.circuit_breaker.opened"

    def test_threshold_below_1_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CircuitBreakerOpened(
                tenant_id=TENANT,
                service_name="svc",
                failure_count=0,
                threshold=0,
                window_ms=1000,
            )


class TestCircuitBreakerClosed:
    def test_instantiation(self) -> None:
        evt = CircuitBreakerClosed(
            tenant_id=TENANT,
            service_name="llm-adapter",
            recovered_after_ms=30000,
            probe_success_count=1,
        )
        assert evt.event_type == "reliability.circuit_breaker.closed"


# ---------------------------------------------------------------------------
# Compliance events
# ---------------------------------------------------------------------------


class TestConsentRecorded:
    def test_instantiation(self) -> None:
        evt = ConsentRecorded(
            tenant_id=TENANT,
            customer_id=CUSTOMER,
            consent_type="CONTACT",
            granted_at=NOW,
            channel="ivr",
            granted_by_actor="ivr-system",
        )
        assert evt.event_type == "compliance.consent.recorded"


class TestConsentRevoked:
    def test_instantiation(self) -> None:
        evt = ConsentRevoked(
            tenant_id=TENANT,
            customer_id=CUSTOMER,
            consent_type="CONTACT",
            revoked_at=NOW,
            revoked_by_actor="customer-portal",
        )
        assert evt.event_type == "compliance.consent.revoked"
        assert evt.reason == ""


class TestPolicyDecisionMade:
    def test_instantiation(self) -> None:
        evt = PolicyDecisionMade(
            tenant_id=TENANT,
            call_id=CALL,
            rule_id="DPDP-CONSENT-CHECK",
            decision="ALLOW",
            explanation="Consent status GRANTED for CONTACT.",
        )
        assert evt.event_type == "compliance.policy.decision_made"


class TestAuditEventEmitted:
    def test_instantiation(self) -> None:
        evt = AuditEventEmitted(
            tenant_id=TENANT,
            actor_id="user-abc",
            action="customer.create",
            resource_type="Customer",
            resource_id=CUSTOMER,
            outcome="SUCCESS",
        )
        assert evt.event_type == "compliance.audit.event_emitted"
        assert evt.ip_address == ""


class TestPIIRedacted:
    def test_instantiation(self) -> None:
        evt = PIIRedacted(
            tenant_id=TENANT,
            resource_type="CallTranscript",
            resource_id="tr-001",
            fields_redacted=("phone_number", "aadhaar_number"),
            redaction_count=3,
        )
        assert evt.event_type == "compliance.pii.redacted"
        assert len(evt.fields_redacted) == 2

    def test_default_fields_redacted(self) -> None:
        evt = PIIRedacted(
            tenant_id=TENANT,
            resource_type="AuditLog",
            resource_id="al-001",
            redaction_count=0,
        )
        assert evt.fields_redacted == ()


class TestDataErasureRequested:
    def test_instantiation(self) -> None:
        evt = DataErasureRequested(
            tenant_id=TENANT,
            customer_id=CUSTOMER,
            requested_by="portal-session-xyz",
            requested_at=NOW,
            regulation="DPDP",
            due_by=datetime(2026, 7, 30, 0, 0, 0, tzinfo=UTC),
        )
        assert evt.event_type == "compliance.data.erasure_requested"


# ---------------------------------------------------------------------------
# SaaS events
# ---------------------------------------------------------------------------


class TestTenantProvisioned:
    def test_instantiation(self) -> None:
        evt = TenantProvisioned(
            tenant_id=TENANT,
            org_name="Acme Collections Pvt Ltd",
            tier="ENTERPRISE",
            provisioned_by="admin-user-1",
            isolation_profile="DEDICATED_SCHEMA",
        )
        assert evt.event_type == "saas.tenant.provisioned"


class TestTenantSuspended:
    def test_instantiation(self) -> None:
        evt = TenantSuspended(
            tenant_id=TENANT,
            reason="PAYMENT_OVERDUE",
            suspended_by="billing-system",
        )
        assert evt.event_type == "saas.tenant.suspended"
        assert evt.reactivation_date is None


class TestCustomerCreated:
    def test_instantiation(self) -> None:
        evt = CustomerCreated(
            tenant_id=TENANT,
            customer_id=CUSTOMER,
            crm_id="CRM-12345",
            name="Ravi Kumar",
        )
        assert evt.event_type == "saas.customer.created"
        assert evt.preferred_language == "en"


class TestLoanAccountUpdated:
    def test_instantiation(self) -> None:
        evt = LoanAccountUpdated(
            tenant_id=TENANT,
            customer_id=CUSTOMER,
            loan_account_id="LA-9999",
            changed_fields=("dpd", "status"),
            updated_by="sync-job",
        )
        assert evt.event_type == "saas.loan_account.updated"


class TestPTPCreated:
    def test_instantiation(self) -> None:
        evt = PTPCreated(
            tenant_id=TENANT,
            call_id=CALL,
            customer_id=CUSTOMER,
            loan_account_id="LA-9999",
            promised_amount_minor=500000,
            currency="INR",
            promise_date=datetime(2026, 7, 10, 0, 0, 0, tzinfo=UTC),
        )
        assert evt.event_type == "saas.ptp.created"

    def test_negative_amount_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PTPCreated(
                tenant_id=TENANT,
                call_id=CALL,
                customer_id=CUSTOMER,
                loan_account_id="LA-9999",
                promised_amount_minor=-1,
                currency="INR",
                promise_date=NOW,
            )


class TestCampaignStarted:
    def test_instantiation(self) -> None:
        evt = CampaignStarted(
            tenant_id=TENANT,
            campaign_id=CAMPAIGN,
            campaign_name="June Collections Wave 3",
            target_call_count=2500,
            started_by="admin-user-1",
        )
        assert evt.event_type == "saas.campaign.started"


class TestCallDispositioned:
    def test_instantiation(self) -> None:
        evt = CallDispositioned(
            tenant_id=TENANT,
            call_id=CALL,
            customer_id=CUSTOMER,
            loan_account_id="LA-9999",
            outcome_code="PTP_MADE",
            duration_ms=90000,
            dispositioned_at=NOW,
        )
        assert evt.event_type == "saas.call.dispositioned"


class TestUsageEventRecorded:
    def test_instantiation(self) -> None:
        evt = UsageEventRecorded(
            tenant_id=TENANT,
            unit_type="CALL_MINUTE",
            quantity=3,
            occurred_at_bucket="2026-06-30T14:00:00Z",
        )
        assert evt.event_type == "saas.usage.event_recorded"
        assert evt.resource_id == ""


class TestBillingInvoiceGenerated:
    def test_instantiation(self) -> None:
        evt = BillingInvoiceGenerated(
            tenant_id=TENANT,
            invoice_id="INV-2026-06",
            billing_period_start=datetime(2026, 6, 1, tzinfo=UTC),
            billing_period_end=datetime(2026, 6, 30, tzinfo=UTC),
            total_amount_minor=4999900,
            currency="INR",
            line_item_count=5,
        )
        assert evt.event_type == "saas.billing.invoice_generated"


# ---------------------------------------------------------------------------
# Cross-cutting: auto-generated fields
# ---------------------------------------------------------------------------


class TestDomainEventBaseFields:
    def test_event_id_is_auto_uuid(self) -> None:
        e1 = TurnStarted(
            tenant_id=TENANT,
            call_id=CALL,
            turn_id="t-1",
            turn_index=0,
            role="customer",
        )
        e2 = TurnStarted(
            tenant_id=TENANT,
            call_id=CALL,
            turn_id="t-2",
            turn_index=1,
            role="customer",
        )
        assert e1.event_id != e2.event_id

    def test_occurred_at_is_set(self) -> None:
        evt = TurnStarted(
            tenant_id=TENANT,
            call_id=CALL,
            turn_id="t-1",
            turn_index=0,
            role="customer",
        )
        assert evt.occurred_at is not None

    def test_tenant_id_required(self) -> None:
        with pytest.raises(ValidationError):
            TurnStarted(call_id=CALL, turn_id="t-1", turn_index=0, role="customer")  # type: ignore[call-arg]
