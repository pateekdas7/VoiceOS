#!/usr/bin/env python3
"""Print `export VAR=value` lines for the three datastore connection strings,
fetched from Vault KV and URL-encoded, for sourcing via process substitution.

This is the canonical way to populate POSTGRES_DSN/REDIS_URL/MONGODB_URI on
the CPU node (Sprint-019+) — no service or operator should ever hand-build
one of these strings, since a password containing '/'/'+'/'=' silently
breaks naive interpolation (see CPU_NODE_STATE.md §18).

Usage:
    source <(python3 scripts/vault/gen_env.py)
    pytest tests/integration/ -m regression

Requires VAULT_ADDR (defaults to the CPU node's loopback Vault) and a
voiceos-app-policy-scoped VAULT_TOKEN — see scripts/vault/bootstrap_vault.sh.
Never prints anything except the export lines to stdout — nothing here
should be captured/logged in a way that exposes the printed values other
than the caller's own shell environment.
"""

from __future__ import annotations

import json
import os
import urllib.parse

import hvac

with open("/opt/vault/app_token.json") as f:
    token = json.load(f)["auth"]["client_token"]

client = hvac.Client(url=os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200"), token=token)


def _get(path: str) -> str:
    resp = client.secrets.kv.v2.read_secret_version(path=path, mount_point="secret")
    value: str = resp["data"]["data"]["value"]
    return value


pg_pw = urllib.parse.quote(_get("voiceos/postgres"), safe="")
redis_pw = urllib.parse.quote(_get("voiceos/redis"), safe="")
mongo_pw = urllib.parse.quote(_get("voiceos/mongodb"), safe="")

print(f"export POSTGRES_DSN='postgresql://voiceos:{pg_pw}@localhost:5432/voiceos'")
print(f"export REDIS_URL='redis://:{redis_pw}@localhost:6379/0'")
print(f"export MONGODB_URI='mongodb://voiceos:{mongo_pw}@localhost:27017/voiceos?authSource=voiceos'")
print("export PYTHONPATH=/opt/voiceos/app")
