"""PolicyEngine — the Policy Decision Point (PDP) (V4 Ch4).

Evaluation pipeline (V4 Ch4 §4.8/§4.9, Sprint-017 spec):
  1. PolicyRequest received.
  2. Resolve applicable PolicySets (global + tenant + campaign).
  3. Redis cache lookup for each scope's active-rule-id list.
  4. Postgres fallback on cache miss (``PolicyRepository``), then cache the result.
  5. Deny-override evaluation across all matched rules.
  6. PolicyDecision generation (+ latency/decision metrics).
  7. EventBus audit emission for every consequential (non-PERMIT) decision.
  8. Audit persistence (``AuditRepository``).

Every dependency beyond the six built-in packs is optional and additive
(``None`` preserves pure in-process, no-I/O evaluation) — the same pattern
used by every reliability-layer component since Sprint-013
(``RedisEventBusAdapter``, ``IdempotencyGuard``, circuit breakers, etc.).

Architecture: V4 Ch4 (Policy Engine Architecture).
"""

from __future__ import annotations

import json
import time
from typing import Any

from src.libs.contracts.primitives import TenantId
from src.libs.event_bus.publisher import Publisher
from src.libs.redis_client.ttl_guard import TTLGuard

from .break_glass import BreakGlassDirective, BreakGlassPolicy
from .decision import PolicyDecision, PolicyOutcome, most_restrictive
from .inheritance import PolicyInheritance
from .packs import (
    AdminPolicyPack,
    AIGovernancePolicyPack,
    AuthorizationPolicyPack,
    BillingPolicyPack,
    ConversationalPolicyPack,
    DPDPPolicyPack,
    RBIPolicyPack,
    SaaSPolicyPack,
)
from .policy_set import PolicySet
from .rule import PolicyRequest, PolicyRule

POLICY_DECISION_MADE_EVENT_TYPE = "compliance.policy.decision_made"

# Outcomes that are always written to the audit trail (V4 Ch11 §11.7: "all
# ALLOW and DENY decisions logged"). PERMIT is also audited when at least one
# rule explicitly matched — "PERMIT-by-default" (no rule matched, empty
# matching_rules) is excluded to avoid flooding the audit log with the
# steady-state no-op case.
_ALWAYS_AUDITED_OUTCOMES = (PolicyOutcome.DENY, PolicyOutcome.FORBID, PolicyOutcome.REQUIRE)
_AUDITED_OUTCOMES = _ALWAYS_AUDITED_OUTCOMES  # kept for backward-compat with tests

DEFAULT_CACHE_TTL_SECONDS = 30
"""V4 Ch4 §4.13 ``decision_cache_ttl_s: 30``."""

_CACHE_KEY_PREFIX = "policy:ruleset"


def _default_registry() -> dict[str, PolicyRule]:
    """Build the built-in rule registry from all six policy packs.

    The PDP's rule *definitions* (conditions/effects) are Python code — the
    business logic they encode is not expressible as pure cacheable data
    without a full policy DSL interpreter, which is out of this sprint's
    scope. What Redis/Postgres cache and persist (§7.7 rules() below) is the
    *activation*: which rule_ids are enabled for which scope. This registry
    is the compiled lookup from rule_id back to its rule object (V4 Ch4 §4.8
    "Compiled policy cache").
    """
    registry: dict[str, PolicyRule] = {}
    for pack in (
        RBIPolicyPack,
        DPDPPolicyPack,
        AuthorizationPolicyPack,
        AIGovernancePolicyPack,
        ConversationalPolicyPack,
        SaaSPolicyPack,
        BillingPolicyPack,
        AdminPolicyPack,
    ):
        for rule in pack.rules():
            registry[rule.rule_id] = rule
    return registry


class PolicyEngine:
    """The unified Policy Decision Point — evaluates a PolicyRequest against
    every applicable PolicySet and returns a single PolicyDecision.

    Args:
        redis: Redis-compatible client (real or ``FakeRedisClient``) used to
            cache each scope's compiled active-rule-id list. ``None`` skips
            the cache tier entirely (every evaluation loads from
            ``policy_repository`` or the all-rules-active default).
        policy_repository: Postgres-backed rule-activation store. ``None``
            falls back to "every built-in rule is active globally."
        publisher: EventBus publisher for ``PolicyDecisionMade`` audit
            events. ``None`` skips event emission.
        audit_repository: Immutable audit-log writer. ``None`` skips
            Postgres audit persistence.
        cache_ttl_seconds: TTL applied to cached rule-id lists (V4 Ch4 §4.13).
        break_glass: The BreakGlassPolicy this engine defers emergency
            overrides to. Defaults to the architecture's 2-approver/60-minute
            configuration (V4 Ch4 §4.13).
    """

    def __init__(
        self,
        redis: Any | None = None,
        policy_repository: Any | None = None,
        publisher: Publisher | None = None,
        audit_repository: Any | None = None,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        break_glass: BreakGlassPolicy | None = None,
        policy_version: str = "1",
    ) -> None:
        self._redis = redis
        self._ttl_guard = TTLGuard(redis) if redis is not None else None
        self._policy_repository = policy_repository
        self._publisher = publisher
        self._audit_repository = audit_repository
        self._cache_ttl_seconds = cache_ttl_seconds
        self._break_glass = break_glass or BreakGlassPolicy()
        self._policy_version = policy_version
        self._registry = _default_registry()

    @property
    def registry(self) -> dict[str, PolicyRule]:
        """The compiled built-in rule_id -> PolicyRule registry (read-only use)."""
        return self._registry

    # ------------------------------------------------------------------
    # Public evaluation API
    # ------------------------------------------------------------------

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        """Evaluate ``request`` against the resolved global→tenant→campaign
        rule set and return a single PolicyDecision (V4 Ch4 §4.7 ``evaluate``).
        """
        from .metrics import record_decision, record_latency_ms

        start = time.perf_counter()

        global_set = self._load_policy_set("global", None)
        tenant_set = self._load_policy_set("tenant", request.tenant_id) if request.tenant_id else None
        campaign_set = self._load_policy_set("campaign", request.campaign_id) if request.campaign_id else None

        all_rules = PolicyInheritance.resolve(global_set, tenant_set, campaign_set)
        # A PolicyRequest names the single domain it is asking about (V4 Ch4
        # §4.5 "domain, action, subject, resource, context") — only that
        # domain's rules are consulted, so an 'rbi' admission check is never
        # coincidentally blocked by an unrelated 'authz'/'dpdp' rule whose
        # condition happens to match the same request shape.
        matched = tuple(rule for rule in all_rules if rule.domain == request.domain and rule.matches(request))
        outcome = most_restrictive(rule.effect.outcome for rule in matched)
        obligations = tuple(dict.fromkeys(o for rule in matched for o in rule.effect.obligations))

        decision = PolicyDecision(
            outcome=outcome,
            matching_rules=tuple(rule.rule_id for rule in matched),
            reason=self._build_reason(matched, outcome),
            policy_version=self._policy_version,
            obligations=obligations,
        )

        record_latency_ms((time.perf_counter() - start) * 1000)
        record_decision(outcome.value)

        # Audit explicit PERMIT (rule matched) as well as all restrictive outcomes.
        # Skip PERMIT-by-default (no matching rule) to avoid flooding the log.
        should_audit = outcome in _ALWAYS_AUDITED_OUTCOMES or (
            outcome == PolicyOutcome.PERMIT and bool(decision.matching_rules)
        )
        if should_audit:
            self._audit(request, decision)

        return decision

    def emergency_override(self, directive: BreakGlassDirective, request: PolicyRequest) -> PolicyDecision:
        """Authorize (or deny) a break-glass directive — always audited,
        including a denied attempt (V4 Ch4 §4.12: "every break-glass use is
        a high-severity audit event").
        """
        decision = self._break_glass.authorize(directive)
        self._audit(request, decision)
        return decision

    # ------------------------------------------------------------------
    # Rule-set resolution: Redis cache -> Postgres fallback -> compiled default
    # ------------------------------------------------------------------

    def _load_policy_set(self, scope: str, scope_id: str | None) -> PolicySet:
        from .metrics import record_cache_lookup

        cache_key = f"{_CACHE_KEY_PREFIX}:{scope}:{scope_id or 'global'}"

        cached_ids = self._cache_get(cache_key)
        if cached_ids is not None:
            record_cache_lookup(hit=True)
            rule_ids = cached_ids
        else:
            record_cache_lookup(hit=False)
            rule_ids = self._load_active_rule_ids_from_source(scope, scope_id)
            self._cache_set(cache_key, rule_ids)

        rules = tuple(self._registry[rid] for rid in rule_ids if rid in self._registry)
        return PolicySet(scope=scope, scope_id=scope_id, rules=rules, version=self._policy_version)

    def _load_active_rule_ids_from_source(self, scope: str, scope_id: str | None) -> tuple[str, ...]:
        """Postgres fallback: ``PolicyRepository`` if wired, else every built-in rule."""
        if self._policy_repository is not None:
            result: tuple[str, ...] = tuple(self._policy_repository.load_active_rule_ids(scope, scope_id))
            return result
        # No repository wired (pure in-process mode, Phase 1): every
        # built-in rule is active at the global scope only — tenant/campaign
        # scopes contribute nothing until real policy rows exist for them.
        if scope == "global":
            return tuple(self._registry.keys())
        return ()

    def _cache_get(self, key: str) -> tuple[str, ...] | None:
        if self._redis is None:
            return None
        raw = self._redis.get(key)
        if raw is None:
            return None
        decoded = raw.decode() if isinstance(raw, bytes) else raw
        rule_ids: list[str] = json.loads(decoded)
        return tuple(rule_ids)

    def _cache_set(self, key: str, rule_ids: tuple[str, ...]) -> None:
        if self._ttl_guard is None:
            return
        self._ttl_guard.set(key, json.dumps(list(rule_ids)), ex=self._cache_ttl_seconds)

    # ------------------------------------------------------------------
    # Audit emission + persistence
    # ------------------------------------------------------------------

    def _build_reason(self, matched: tuple[PolicyRule, ...], outcome: PolicyOutcome) -> str:
        if not matched:
            return "no policy rule matched — default PERMIT"
        rule_ids = ", ".join(rule.rule_id for rule in matched)
        return f"outcome {outcome.value} from rule(s): {rule_ids}"

    def _audit(self, request: PolicyRequest, decision: PolicyDecision) -> None:
        call_id = request.context.get("call_id", request.resource)

        if self._publisher is not None and request.tenant_id is not None:
            self._publisher.publish(
                event_type=POLICY_DECISION_MADE_EVENT_TYPE,
                tenant_id=TenantId(request.tenant_id),
                payload={
                    "call_id": call_id,
                    "rule_id": ",".join(decision.matching_rules) or "-",
                    "decision": decision.outcome.value,
                    "explanation": decision.reason,
                    "domain": request.domain,
                    "action": request.action,
                },
                correlation_id=str(call_id),
            )

        if self._audit_repository is not None and request.tenant_id is not None:
            self._audit_repository.append(
                TenantId(request.tenant_id),
                actor_id=request.subject,
                action=f"policy.evaluate.{request.action}",
                resource_type=request.domain,
                resource_id=request.resource,
                outcome=decision.outcome.value,
                event_payload={"matching_rules": list(decision.matching_rules), "reason": decision.reason},
            )
