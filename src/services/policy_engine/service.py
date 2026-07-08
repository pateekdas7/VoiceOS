"""PolicyEngineService — lifecycle façade around the PolicyEngine PDP.

Sprint-017.md describes this as a "gRPC/REST PDP API". Every VoiceOS
service since Sprint-013 (EventBusService, RelationshipMemoryStore,
RecoveryManager, CircuitBreakerRegistry, ...) ships as a library class
consumed in-process rather than a standalone HTTP/gRPC listener —
standalone service processes are Sprint-026 (K8s/Helm) scope, not before
(see CPU_NODE_STATE.md §8.1). This service follows that established
precedent: it is the in-process PDP façade every enforcement point
(ConversationEngine, DialoguePolicyEngine, ...) consults directly, with the
identical public surface (``evaluate``/``emergency_override``) a future
gRPC/REST wrapper would expose unchanged.

Architecture: V4 Ch4 (Policy Engine Architecture) §4.7 (Public Interfaces).
"""

from __future__ import annotations

from typing import Any

from src.libs.event_bus.publisher import Publisher

from .break_glass import BreakGlassDirective, BreakGlassPolicy
from .decision import PolicyDecision
from .engine import PolicyEngine
from .rule import PolicyRequest


class PolicyEngineService:
    """The PDP entry point every enforcement point (PEP) in VoiceOS consults."""

    def __init__(self, engine: PolicyEngine) -> None:
        self._engine = engine

    @classmethod
    def create(
        cls,
        redis: Any | None = None,
        policy_repository: Any | None = None,
        publisher: Publisher | None = None,
        audit_repository: Any | None = None,
        break_glass: BreakGlassPolicy | None = None,
    ) -> PolicyEngineService:
        """Factory: build a PolicyEngineService with the given (optional) backends."""
        return cls(
            PolicyEngine(
                redis=redis,
                policy_repository=policy_repository,
                publisher=publisher,
                audit_repository=audit_repository,
                break_glass=break_glass,
            )
        )

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        """Evaluate a PolicyRequest and return the PDP's decision (V4 Ch4 §4.7)."""
        return self._engine.evaluate(request)

    def emergency_override(self, directive: BreakGlassDirective, request: PolicyRequest) -> PolicyDecision:
        """Authorize an emergency break-glass override (V4 Ch4 §4.7 ``emergency``)."""
        return self._engine.emergency_override(directive, request)

    def check_call_admission(
        self,
        tenant_id: str,
        call_id: str,
        subject: str = "conversation_engine",
        hour: int | None = None,
        calls_today_count: int = 0,
    ) -> PolicyDecision:
        """Convenience wrapper: evaluate whether a call may be dialed right now.

        This is the query ConversationEngine issues before call start (Sprint-017
        integration wiring): RBI ``CALLING_HOURS``/``CALLING_FREQUENCY`` are the
        rules in scope. Returns DENY when the call falls outside the permitted
        window or the customer's daily call cap has been reached.

        Uses ``action="admit_call"`` (distinct from ``"start_call"``) so this
        pre-dial check is judged purely on calling-hours/frequency — not on
        the call-opening script obligations (``DISCLOSURE_REQUIRED``,
        ``RECORDING_CONSENT``) that only become relevant once the call is
        actually connected (see ``packs/rbi.py`` module docstring).
        """
        context: dict[str, Any] = {"call_id": call_id, "calls_today_count": calls_today_count}
        if hour is not None:
            context["hour"] = hour
        request = PolicyRequest(
            domain="rbi",
            action="admit_call",
            subject=subject,
            resource=call_id,
            tenant_id=tenant_id,
            context=context,
        )
        return self._engine.evaluate(request)

    def check_tenant_active(
        self,
        tenant_id: str,
        is_active: bool,
        call_id: str = "",
        subject: str = "tenant_management",
    ) -> PolicyDecision:
        """Convenience wrapper: evaluate whether ``tenant_id`` may admit a new call.

        ``is_active`` is resolved by the caller (``TenantService``/
        ``TenantSuspender``) — the Policy Engine has no direct dependency on
        ``src.services.tenant_management`` (same caller-resolves-the-fact
        pattern as ``calls_today_count`` in ``check_call_admission``).
        """
        request = PolicyRequest(
            domain="tenant",
            action="admit_call",
            subject=subject,
            resource=call_id or tenant_id,
            tenant_id=tenant_id,
            context={"tenant_active": is_active, "call_id": call_id},
        )
        return self._engine.evaluate(request)

    def check_entitlement(
        self,
        tenant_id: str,
        feature: str,
        tier: str,
        usage_quantity: int | None = None,
        usage_limit: int | None = None,
        trial_expired: bool = False,
        subject: str = "billing_service",
    ) -> PolicyDecision:
        """Convenience wrapper: evaluate whether ``tenant_id`` may consume ``feature`` now.

        This is the query ``EntitlementEngine``/``UsageLimitEnforcer`` issue
        before allowing a billable operation (Sprint-024): DENY when
        ``usage_quantity`` has reached ``usage_limit`` (``BillingPolicyPack.
        USAGE_LIMIT_EXCEEDED``) or when a TRIAL subscription's window has
        elapsed (``BillingPolicyPack.TRIAL_EXPIRED``). Current usage and the
        tier's limit are resolved by the caller — the Policy Engine has no
        direct dependency on ``src.services.billing``/``src.services.metering``
        (same caller-resolves-the-fact pattern as ``check_call_admission``).
        """
        request = PolicyRequest(
            domain="billing",
            action="use_feature",
            subject=subject,
            resource=feature,
            tenant_id=tenant_id,
            context={
                "tier": tier,
                "usage_quantity": usage_quantity,
                "usage_limit": usage_limit,
                "trial_expired": trial_expired,
            },
        )
        return self._engine.evaluate(request)

    def check_campaign_approval(
        self,
        tenant_id: str,
        campaign_id: str,
        campaign_status: str,
        subject: str = "admin_portal",
    ) -> PolicyDecision:
        """Convenience wrapper: evaluate whether ``campaign_id`` may be approved now.

        This is the query ``CampaignAdminController.approve`` issues before
        finalizing an approval (Sprint-025): DENY when the campaign has not
        first been submitted for review (``AdminPolicyPack.
        CAMPAIGN_APPROVAL_REQUIRES_REVIEW``). ``campaign_status`` is resolved
        by the caller — the Policy Engine has no direct dependency on
        ``src.services.campaign_management`` (same caller-resolves-the-fact
        pattern as ``check_call_admission``).
        """
        request = PolicyRequest(
            domain="campaign_admin",
            action="approve",
            subject=subject,
            resource=campaign_id,
            tenant_id=tenant_id,
            context={"campaign_status": campaign_status},
        )
        return self._engine.evaluate(request)

    def check_conversational_rule(self, rule_id: str, context: dict[str, Any]) -> str:
        """Evaluate a single named rule against ``context`` and return its outcome.

        Satisfies the structural ``PolicyLookupPort`` that
        ``src.engines.dialogue_policy.engine.DialoguePolicyEngine.evaluate()``
        accepts (Sprint-017) — engines never import ``src/services/``
        (check_boundaries.py Rule 2), so this method's shape (plain
        ``str``/``dict`` in, ``str`` out) is what lets a composition root wire
        a real PolicyEngineService into that engine without a boundary
        violation on either side.
        """
        rule = self._engine.registry.get(rule_id)
        if rule is None:
            return "PERMIT"
        # "start_call" — every rule this hook currently supports (e.g.
        # RBI-RECORDING-CONSENT) is scoped to the call-opening action
        # (see packs/rbi.py's action-scoping note).
        request = PolicyRequest(
            domain=rule.domain, action="start_call", subject="dialogue_policy_engine", resource=rule_id, context=context
        )
        return rule.decide(request).value

    @property
    def engine(self) -> PolicyEngine:
        """The underlying PolicyEngine (for advanced/direct evaluation)."""
        return self._engine
