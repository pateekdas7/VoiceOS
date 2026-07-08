#!/usr/bin/env python3
"""Seed the Policy Engine's built-in rule catalog into Postgres + warm the Redis cache.

Loads every rule_id from the five policy packs (RBI, DPDP, Authorization,
AIGovernance, Conversational — 18 rules total) into the ``policies`` table
as globally active (``PolicyRepository.upsert_policy``), then evaluates one
no-op PolicyRequest per domain through a real ``PolicyEngine`` (backed by
the same Postgres repository + a real Redis client) so each domain's
compiled rule-id list is populated in the Redis cache before any real
traffic queries it.

Usage:
    POSTGRES_DSN=postgresql://voiceos:voiceos_pw@localhost:5432/voiceos \\
    REDIS_URL=redis://localhost:6379/0 \\
    python scripts/seed_policies.py --env production
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import redis as redis_lib

from src.libs.repositories.policy import PolicyRepository
from src.services.policy_engine.engine import PolicyEngine
from src.services.policy_engine.packs import (
    AIGovernancePolicyPack,
    AuthorizationPolicyPack,
    ConversationalPolicyPack,
    DPDPPolicyPack,
    RBIPolicyPack,
)
from src.services.policy_engine.rule import PolicyRequest

POSTGRES_DSN = os.environ.get("POSTGRES_DSN", "postgresql://voiceos:voiceos_pw@localhost:5432/voiceos")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

_ALL_PACKS = (RBIPolicyPack, DPDPPolicyPack, AuthorizationPolicyPack, AIGovernancePolicyPack, ConversationalPolicyPack)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="development", help="Environment label (logged only; no behavior change).")
    args = parser.parse_args()

    pg_conn = psycopg2.connect(POSTGRES_DSN)
    redis_conn = redis_lib.Redis.from_url(REDIS_URL, decode_responses=False, protocol=2)
    print(f"[seed_policies] env={args.env} postgres={POSTGRES_DSN.split('@')[-1]} redis={REDIS_URL}")

    repository = PolicyRepository(pg_conn)
    seeded = 0
    for pack in _ALL_PACKS:
        for rule in pack.rules():
            repository.upsert_policy(rule.rule_id, rule.pack, scope="global")
            seeded += 1
    print(f"[seed_policies] upserted {seeded} globally-active policy rows across {len(_ALL_PACKS)} packs")

    engine = PolicyEngine(redis=redis_conn, policy_repository=repository)
    domains = sorted({rule.domain for pack in _ALL_PACKS for rule in pack.rules()})
    for domain in domains:
        engine.evaluate(
            PolicyRequest(domain=domain, action="__seed_policies_warmup__", subject="seed_policies", resource="-")
        )
    print(f"[seed_policies] warmed the Redis rule-set cache for domains: {', '.join(domains)}")

    pg_conn.close()
    print("[seed_policies] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
