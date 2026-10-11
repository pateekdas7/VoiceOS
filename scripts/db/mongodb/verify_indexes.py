#!/usr/bin/env python3
"""Verify MongoDB collection indexes are present post-server-replacement (Phase 8f).

Connects to MongoDB, loads the canonical index specs from the Sprint-002 JSON
files, and for each expected index checks whether it is present in
``collection.index_information()``.  Reports missing indexes and optionally
re-creates them.

Usage:
    # Report only (non-destructive)
    MONGO_URI=mongodb://localhost:27017/voiceos python scripts/db/mongodb/verify_indexes.py

    # Report and re-create any missing indexes
    MONGO_URI=mongodb://localhost:27017/voiceos python scripts/db/mongodb/verify_indexes.py --fix

    # Dry-run: print what would be checked/created without touching MongoDB
    python scripts/db/mongodb/verify_indexes.py --dry-run

Reads MONGO_URI (canonical deployment name) falling back to MONGODB_URI
(pytest integration-test fixtures).

Architecture: V3 Ch5 (MongoDB Document Store); Phase 8f (post-server-replacement
index verification).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
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
    """Load all collection index specs from the canonical JSON files."""
    specs: list[dict[str, Any]] = []
    for fname in INDEX_FILES:
        path = MONGODB_DIR / fname
        specs.extend(json.loads(path.read_text(encoding="utf-8")))
    return specs


@dataclass
class VerificationResult:
    collection: str
    expected: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    recreated: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.errors


def verify_collection(
    collection: Any,
    entry: dict[str, Any],
    *,
    fix: bool = False,
    dry_run: bool = False,
) -> VerificationResult:
    """Verify (and optionally fix) indexes for one collection.

    Args:
        collection: pymongo Collection (or None in dry-run mode).
        entry: One collection's spec dict (from the JSON index files).
        fix: Re-create any missing indexes when True.
        dry_run: Print planned actions without querying MongoDB.

    Returns:
        VerificationResult populated with present/missing/recreated lists.
    """
    collection_name = entry["collection"]
    result = VerificationResult(collection=collection_name)

    if dry_run:
        for idx in entry["indexes"]:
            result.expected.append(idx["name"])
            print(f"[dry-run] would verify index '{idx['name']}' on {collection_name}")
            if fix:
                print(f"[dry-run] would re-create if missing: {idx['name']}")
        return result

    existing: dict[str, Any] = collection.index_information()

    for idx in entry["indexes"]:
        name = idx["name"]
        result.expected.append(name)
        if name in existing:
            result.present.append(name)
        else:
            result.missing.append(name)
            if fix:
                keys: list[tuple[str, int]] = list(idx["keys"].items())
                options = {k: v for k, v in idx.get("options", {}).items() if k != "comment"}
                options["name"] = name
                try:
                    collection.create_index(keys, **options)
                    result.recreated.append(name)
                    print(f"  RECREATED: {collection_name}.{name}")
                except Exception as exc:
                    msg = f"{collection_name}.{name}: {exc}"
                    result.errors.append(msg)
                    print(f"  ERROR recreating {msg}", file=sys.stderr)

    return result


def verify_all(
    db: Any,
    specs: list[dict[str, Any]],
    *,
    fix: bool = False,
    dry_run: bool = False,
) -> list[VerificationResult]:
    """Run verification across every collection in ``specs``.

    Args:
        db: pymongo Database instance (or None in dry-run mode).
        specs: All collection specs from load_index_specs().
        fix: Re-create missing indexes when True.
        dry_run: Print planned actions without touching MongoDB.

    Returns:
        One VerificationResult per collection entry.
    """
    results: list[VerificationResult] = []
    for entry in specs:
        collection_name = entry["collection"]
        print(f"\nChecking collection: {collection_name}")
        collection = None if dry_run else db[collection_name]
        result = verify_collection(collection, entry, fix=fix, dry_run=dry_run)
        results.append(result)

        if not dry_run:
            for name in result.present:
                print(f"  OK:      {collection_name}.{name}")
            for name in result.missing:
                if name not in result.recreated:
                    print(f"  MISSING: {collection_name}.{name}", file=sys.stderr)

    return results


def print_summary(results: list[VerificationResult]) -> None:
    """Print a compact summary table of the verification run."""
    total_expected = sum(len(r.expected) for r in results)
    total_present = sum(len(r.present) for r in results)
    total_missing = sum(len(r.missing) for r in results)
    total_recreated = sum(len(r.recreated) for r in results)
    total_errors = sum(len(r.errors) for r in results)

    print("\n" + "=" * 60)
    print("MongoDB Index Verification Summary")
    print("=" * 60)
    print(f"  Collections checked : {len(results)}")
    print(f"  Expected indexes    : {total_expected}")
    print(f"  Present             : {total_present}")
    print(f"  Missing             : {total_missing}")
    print(f"  Recreated           : {total_recreated}")
    print(f"  Errors              : {total_errors}")
    print("=" * 60)

    if total_missing > 0 and total_recreated < total_missing:
        remaining = total_missing - total_recreated
        print(f"\nWARNING: {remaining} index(es) still missing.")
        print("Run with --fix to re-create them automatically.")
    elif total_missing > 0 and total_recreated == total_missing:
        print("\nAll missing indexes have been re-created successfully.")
    elif total_errors > 0:
        print(f"\nERROR: {total_errors} recreation failure(s). Review output above.")
    else:
        print("\nAll indexes verified present. No action required.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fix", action="store_true", help="Re-create any missing indexes automatically")
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without touching MongoDB")
    args = parser.parse_args()

    if args.dry_run and args.fix:
        print("ERROR: --dry-run and --fix are mutually exclusive.", file=sys.stderr)
        return 1

    mongodb_uri = os.environ.get("MONGO_URI", "") or os.environ.get("MONGODB_URI", "")
    if not mongodb_uri and not args.dry_run:
        print(
            "ERROR: MONGO_URI (or MONGODB_URI) not set.\n"
            "Example: MONGO_URI=mongodb://localhost:27017/voiceos python verify_indexes.py",
            file=sys.stderr,
        )
        return 1

    specs = load_index_specs()

    if args.dry_run:
        print(f"Dry-run: {len(specs)} collection spec(s) loaded from {MONGODB_DIR}")
        results = verify_all(db=None, specs=specs, fix=args.fix, dry_run=True)
        print_summary(results)
        return 0

    from pymongo import MongoClient  # type: ignore[import-untyped]

    client: MongoClient[Any] = MongoClient(mongodb_uri)
    try:
        db = client.get_default_database()
        print(f"Connected to MongoDB: {mongodb_uri.split('@')[-1]}")  # strip credentials from log
        results = verify_all(db, specs, fix=args.fix)
    finally:
        client.close()

    print_summary(results)

    all_ok = all(r.ok for r in results)
    any_remaining_missing = any(
        m for r in results for m in r.missing if m not in r.recreated
    )
    return 0 if (all_ok or (args.fix and not any_remaining_missing and all(not r.errors for r in results))) else 1


if __name__ == "__main__":
    sys.exit(main())
