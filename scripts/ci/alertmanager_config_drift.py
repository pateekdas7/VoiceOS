#!/usr/bin/env python3
"""Phase 2 / G2 — Alertmanager config drift check.

Enforces that `monitoring/grafana/alerts/alertmanager.yml` is the single source
of truth for Alertmanager routing:

1. The deprecated stub at `scripts/k8s/alertmanager-config.DEPRECATED.yaml`
   must not be referenced by any file in `infra/` or `scripts/` as an apply
   target. Any `kubectl apply -f` or `kustomize` reference pointing at it is
   a hard fail.

2. The ConfigMap name used by the live Deployment
   (`infra/k8s/observability/alertmanager.yaml`) must be
   `alertmanager-config-template` (volume reference), i.e. generated from
   the Grafana folder file at deploy time, not from any committed ConfigMap
   object in the repo.

Exits 0 if clean, non-zero with a diagnostic if drift is detected.
"""
from __future__ import annotations

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

SOT = REPO_ROOT / "monitoring" / "grafana" / "alerts" / "alertmanager.yml"
DEPRECATED = REPO_ROOT / "scripts" / "k8s" / "alertmanager-config.DEPRECATED.yaml"
DEPLOYMENT = REPO_ROOT / "infra" / "k8s" / "observability" / "alertmanager.yaml"


def _fail(msg: str) -> None:
    print(f"[alertmanager-drift] FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"[alertmanager-drift] OK:   {msg}")


def main() -> int:
    if not SOT.exists():
        _fail(f"Single source of truth missing: {SOT.relative_to(REPO_ROOT)}")

    if not DEPLOYMENT.exists():
        _fail(f"Deployment manifest missing: {DEPLOYMENT.relative_to(REPO_ROOT)}")

    if not DEPRECATED.exists():
        _fail(
            f"Deprecated stub missing: {DEPRECATED.relative_to(REPO_ROOT)} — "
            "either it was renamed back (dangerous) or the Phase 2 refactor was reverted."
        )
    _ok(f"Deprecated stub retained for history: {DEPRECATED.relative_to(REPO_ROOT)}")

    # Rule 1 — no deploy manifest applies the deprecated stub. Only YAML/sh/
    # Makefile targets are deploy surfaces; markdown + Python docs may mention
    # the historical name without triggering drift.
    deploy_suffixes = (".yaml", ".yml", ".sh", ".mk", "Makefile")
    pattern = re.compile(r"alertmanager-config\.yaml\b")
    for p in REPO_ROOT.rglob("*"):
        if not p.is_file():
            continue
        if p.name.endswith(".DEPRECATED.yaml"):
            continue
        if ".git/" in str(p):
            continue
        if not (p.name.endswith(deploy_suffixes) or p.name == "Makefile"):
            continue
        try:
            text = p.read_text(errors="ignore")
        except Exception:
            continue
        if pattern.search(text):
            _fail(
                f"{p.relative_to(REPO_ROOT)} references the deprecated stub "
                "`alertmanager-config.yaml`. Remove the reference — the single "
                "source of truth is monitoring/grafana/alerts/alertmanager.yml."
            )
    _ok("No deploy manifest references the deprecated stub by filename.")

    # Rule 2 — the deployment uses the ConfigMap name `alertmanager-config-template`.
    dep_text = DEPLOYMENT.read_text()
    if "alertmanager-config-template" not in dep_text:
        _fail(
            f"{DEPLOYMENT.relative_to(REPO_ROOT)} must mount ConfigMap "
            "`alertmanager-config-template` generated from the Grafana SoT."
        )
    _ok("Deployment references the canonical ConfigMap name.")

    # Rule 3 — the deprecated stub was de-named so a repo-wide kubectl apply
    # cannot create the live ConfigMap name.
    dep_stub_text = DEPRECATED.read_text()
    live_name = re.search(r"^\s*name:\s*alertmanager-config-template\s*$", dep_stub_text, re.M)
    if live_name is not None:
        _fail(
            f"{DEPRECATED.relative_to(REPO_ROOT)} still declares the live "
            "ConfigMap name — rename it in metadata.name so applying this file "
            "cannot overwrite the real config."
        )
    _ok("Deprecated stub has a non-live ConfigMap name.")

    _ok("Alertmanager config drift check PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
