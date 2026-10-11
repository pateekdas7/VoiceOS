#!/usr/bin/env python3
"""Phase 6 / D1 — enforce runbook coverage for every Prometheus alert.

Every alert rule under ``monitoring/prometheus/alert_rules/*.yml`` must
declare an ``annotations.runbook_url`` pointing to an existing file in
``docs/runbooks/``. The URL is a *filesystem path* (not an https://
link) so the check does not need network — the ops team can click
through from Grafana because the URL rewrites to the internal wiki at
render time.

Exit codes:
    0 — every alert has a runbook_url and every URL resolves.
    1 — missing annotations or dangling URLs.

Usage::

    python3 scripts/ci/check_runbook_coverage.py
"""
from __future__ import annotations

import pathlib
import sys

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:
    print("[runbook-coverage] FAIL: PyYAML not installed", file=sys.stderr)
    sys.exit(2)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ALERT_DIR = REPO_ROOT / "monitoring" / "prometheus" / "alert_rules"
RUNBOOK_DIR = REPO_ROOT / "docs" / "runbooks"


def _fail(msgs: list[str]) -> int:
    for m in msgs:
        print(f"[runbook-coverage] FAIL: {m}", file=sys.stderr)
    return 1


def _resolve(url: str) -> pathlib.Path | None:
    """Resolve a runbook_url (filesystem relpath from repo root) to an abspath."""
    if url.startswith(("http://", "https://")):
        return None  # external links can't be verified locally
    rel = url.lstrip("/")
    candidate = REPO_ROOT / rel
    return candidate if candidate.exists() else None


def main() -> int:
    if not ALERT_DIR.is_dir():
        return _fail([f"alert rules directory missing: {ALERT_DIR}"])
    if not RUNBOOK_DIR.is_dir():
        return _fail([f"runbooks directory missing: {RUNBOOK_DIR}"])

    missing: list[str] = []
    dangling: list[str] = []
    checked = 0

    for yml in sorted(ALERT_DIR.glob("*.yml")):
        with yml.open("r") as fh:
            doc = yaml.safe_load(fh) or {}
        for group in doc.get("groups", []) or []:
            for rule in group.get("rules", []) or []:
                name = rule.get("alert")
                if not name:
                    continue  # recording rule — skip
                checked += 1
                ann = rule.get("annotations") or {}
                url = ann.get("runbook_url")
                if not url:
                    missing.append(f"{yml.name}::{name} — no annotations.runbook_url")
                    continue
                if url.startswith(("http://", "https://")):
                    continue
                resolved = _resolve(url)
                if resolved is None:
                    dangling.append(
                        f"{yml.name}::{name} — runbook_url {url!r} does not exist in {RUNBOOK_DIR.relative_to(REPO_ROOT)}/"
                    )

    msgs = missing + dangling
    if msgs:
        return _fail(msgs + [f"({len(missing)} missing, {len(dangling)} dangling of {checked} alerts)"])

    print(f"[runbook-coverage] OK: {checked} alert(s), 100% runbook coverage")
    return 0


if __name__ == "__main__":
    sys.exit(main())
