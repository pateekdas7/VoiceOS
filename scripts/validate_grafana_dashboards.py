#!/usr/bin/env python3
"""Validate Grafana dashboard JSON files (Sprint-016).

The sprint spec calls for running ``grafana-dashboard-linter`` — a Node/npm
tool — over ``monitoring/grafana/dashboards/*.json``. This is a pure-Python
codebase with no Node toolchain (V6 Ch2 dependency policy), so this script
is the local equivalent: it validates that every dashboard file is well-formed
JSON and satisfies the structural properties Grafana itself requires
(unique ``uid``, a ``title``, a non-empty ``panels`` array, and every panel
having ``id``, ``title``, ``type``, and at least one Prometheus ``targets`` entry).

Deviation recorded in CHANGELOG.md / CPU_NODE_STATE.md: substitutes this
script for ``grafana-dashboard-linter`` since the latter is unavailable in
a pip-only dependency chain.

Usage:
  python scripts/validate_grafana_dashboards.py
  python scripts/validate_grafana_dashboards.py --dir path/to/dashboards
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REQUIRED_TOP_LEVEL = ("uid", "title", "schemaVersion", "panels")
REQUIRED_PANEL_FIELDS = ("id", "title", "type", "targets")


def validate_dashboard(path: Path) -> list[str]:
    """Validate one dashboard JSON file. Returns a list of error strings (empty if valid)."""
    errors: list[str] = []
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{path.name}: invalid JSON — {exc}"]

    if not isinstance(data, dict):
        return [f"{path.name}: top level must be a JSON object"]

    for field_name in REQUIRED_TOP_LEVEL:
        if field_name not in data:
            errors.append(f"{path.name}: missing required field '{field_name}'")

    panels = data.get("panels", [])
    if not isinstance(panels, list) or not panels:
        errors.append(f"{path.name}: 'panels' must be a non-empty array")
        return errors

    for i, panel in enumerate(panels):
        for field_name in REQUIRED_PANEL_FIELDS:
            if field_name not in panel:
                errors.append(f"{path.name}: panel[{i}] missing required field '{field_name}'")
        targets = panel.get("targets", [])
        if not isinstance(targets, list) or not targets:
            errors.append(f"{path.name}: panel[{i}] ('{panel.get('title', '?')}') has no targets")
        else:
            for j, target in enumerate(targets):
                if not target.get("expr"):
                    errors.append(f"{path.name}: panel[{i}] target[{j}] missing a Prometheus 'expr'")

    return errors


def validate_directory(directory: Path) -> dict[str, list[str]]:
    """Validate every ``*.json`` file in ``directory``. Returns errors keyed by filename."""
    results: dict[str, list[str]] = {}
    seen_uids: dict[str, str] = {}

    for path in sorted(directory.glob("*.json")):
        errors = validate_dashboard(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            uid = data.get("uid")
            if uid:
                if uid in seen_uids:
                    errors.append(f"{path.name}: duplicate uid '{uid}' (also used by {seen_uids[uid]})")
                else:
                    seen_uids[uid] = path.name
        except (json.JSONDecodeError, AttributeError):
            pass
        if errors:
            results[path.name] = errors

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", default="monitoring/grafana/dashboards", metavar="DIR")
    args = parser.parse_args()

    directory = Path(args.dir)
    if not directory.exists():
        print(f"ERROR: dashboard directory not found: {directory}", file=sys.stderr)
        return 1

    files = sorted(directory.glob("*.json"))
    if not files:
        print(f"ERROR: no dashboard JSON files found in {directory}", file=sys.stderr)
        return 1

    results = validate_directory(directory)
    if results:
        print(f"GRAFANA DASHBOARD VALIDATION FAILED: {len(results)} file(s) with errors", file=sys.stderr)
        for errors in results.values():
            for error in errors:
                print(f"  {error}", file=sys.stderr)
        return 1

    print(f"OK: {len(files)} Grafana dashboard(s) validated in {directory}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
