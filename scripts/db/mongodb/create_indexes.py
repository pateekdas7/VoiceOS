#!/usr/bin/env python3
"""Apply MongoDB collection indexes from the Sprint-002 JSON index specs.

The index definitions (response_plans, decision_envelopes, call_transcripts,
call_lineage) were authored in Sprint-002 but never actually applied to a
running MongoDB instance — this script is the missing piece (Sprint-014).

Usage:
    MONGO_URI=mongodb://localhost:27017/voiceos python scripts/db/mongodb/create_indexes.py
    python scripts/db/mongodb/create_indexes.py --dry-run

Reads ``MONGO_URI`` (the deployment scripts' canonical name — see
deployment/cpu/.env.example) falling back to ``MONGODB_URI`` (the name used
by the pytest integration-test fixtures, tests/integration/conftest.py).

pymongo's ``create_index`` is idempotent: re-creating an index with the same
name and identical options is a no-op; requesting the same name with
different options raises ``OperationFailure`` (surfaced, not swallowed).

Architecture: V3 Ch5 (MongoDB Document Store); Sprint-014.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

MONGODB_DIR = Path(__file__).parent

INDEX_FILES = [
    "response_plans_indexes.json",
    "decision_envelopes_indexes.json",
    "call_transcripts_indexes.json",
    "call_lineage_indexes.json",
]


def load_index_specs() -> list[dict[str, Any]]:
    """Load and concatenate all collection index specs from the JSON files."""
    specs: list[dict[str, Any]] = []
    for fname in INDEX_FILES:
        path = MONGODB_DIR / fname
        specs.extend(json.loads(path.read_text(encoding="utf-8")))
    return specs


def create_indexes(db: Any, specs: list[dict[str, Any]], *, dry_run: bool = False) -> list[str]:
    """Create every index in ``specs`` against ``db``.

    Args:
        db: A pymongo Database instance.
        specs: Parsed index specs (see load_index_specs).
        dry_run: If True, print planned actions without executing them.

    Returns:
        The list of "<collection>.<index_name>" identifiers applied.
    """
    applied: list[str] = []
    for entry in specs:
        collection_name = entry["collection"]
        collection = None if dry_run else db[collection_name]
        for idx in entry["indexes"]:
            keys: list[tuple[str, int]] = list(idx["keys"].items())
            options = {k: v for k, v in idx.get("options", {}).items() if k != "comment"}
            options["name"] = idx["name"]
            identifier = f"{collection_name}.{idx['name']}"
            if dry_run:
                print(f"[dry-run] would create_index({keys!r}, {options!r}) on {collection_name}")
            else:
                collection.create_index(keys, **options)
                print(f"OK: created {identifier}")
            applied.append(identifier)
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print planned index creations without applying them")
    args = parser.parse_args()

    mongodb_uri = os.environ.get("MONGO_URI", "") or os.environ.get("MONGODB_URI", "")
    if not mongodb_uri and not args.dry_run:
        print("ERROR: MONGO_URI (or MONGODB_URI) not set. Example: mongodb://localhost:27017/voiceos", file=sys.stderr)
        return 1

    specs = load_index_specs()

    if args.dry_run:
        create_indexes(db=None, specs=specs, dry_run=True)
        return 0

    from pymongo import MongoClient

    client: MongoClient[Any] = MongoClient(mongodb_uri)
    try:
        db = client.get_default_database()
        applied = create_indexes(db, specs)
        print(f"\nApplied {len(applied)} indexes across {len(specs)} collections.")
        for collection_name in (entry["collection"] for entry in specs):
            count = len(db[collection_name].index_information())
            print(f"  {collection_name}: {count} indexes present")
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
