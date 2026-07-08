"""PolicyRepository — Postgres-backed policy rule-activation store (V4 Ch4).

Stores *which* built-in rule_ids are active at which scope (global / tenant
/ campaign) — the rule *definitions* (conditions/effects) are Python code in
``src/services/policy_engine/packs/`` (see that package's engine.py
docstring for the rationale). This repository is the Postgres fallback tier
in the Policy Engine's evaluation pipeline: on a Redis cache miss, the
PolicyEngine loads the active rule_ids for a scope from here and re-caches
the result.

Unlike most repositories, ``tenant_id`` is genuinely optional here (a
``NULL`` value denotes the global scope, which by definition applies to every
tenant) — so this repository does not route through
``BaseRepository._tenant_select``'s mandatory-tenant-id helper the way
per-tenant domain repositories do.

Architecture: V4 Ch4 (Policy Engine Architecture); V4 Ch4 §4.13 (scopes).
"""

from __future__ import annotations

from .base import BaseRepository

_TABLE = "policies"


class PolicyRepository(BaseRepository):
    """Tenant-optional (global/tenant/campaign scoped) policy activation queries."""

    def load_active_rule_ids(self, scope: str, scope_id: str | None) -> tuple[str, ...]:
        """Return every active rule_id for ``scope`` (+ ``scope_id`` if scoped).

        Args:
            scope: 'global' | 'tenant' | 'campaign'.
            scope_id: Required for 'tenant'/'campaign' scope; ignored (must be
                ``None``) for 'global'.
        """
        if scope == "global":
            cur = self._execute(
                f"SELECT policy_id FROM {_TABLE} WHERE scope = %s AND active = TRUE",
                (scope,),
            )
        else:
            column = "tenant_id" if scope == "tenant" else "campaign_id"
            cur = self._execute(
                f"SELECT policy_id FROM {_TABLE} WHERE scope = %s AND {column} = %s AND active = TRUE",
                (scope, scope_id),
            )
        rows: list[tuple[str]] = cur.fetchall()
        return tuple(row[0] for row in rows)

    def upsert_policy(
        self,
        policy_id: str,
        pack: str,
        scope: str,
        scope_id: str | None = None,
        active: bool = True,
    ) -> None:
        """Insert or reactivate/deactivate a (policy_id, scope, scope_id) row.

        Used by ``scripts/seed_policies.py`` to seed the RBI/DPDP/authz/
        ai_governance/conversational packs' rule_ids as globally active.
        """
        tenant_id = scope_id if scope == "tenant" else None
        campaign_id = scope_id if scope == "campaign" else None
        self._execute(
            f"""
            INSERT INTO {_TABLE} (policy_id, pack, scope, tenant_id, campaign_id, active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (policy_id, scope, COALESCE(tenant_id, '00000000-0000-0000-0000-000000000000'),
                         COALESCE(campaign_id, '00000000-0000-0000-0000-000000000000'))
            DO UPDATE SET active = EXCLUDED.active, updated_at = NOW()
            """,
            (policy_id, pack, scope, tenant_id, campaign_id, active),
        )
        self._commit()
