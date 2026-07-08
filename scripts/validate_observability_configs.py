#!/usr/bin/env python3
"""Structural validator for Sprint-027's observability configs.

``promtool``/``amtool`` are not installed in this environment (same class
of gap as Sprint-017's rsync->scp and Sprint-025's
``scripts/validate_openapi.py`` substituting for the unavailable
``openapi-generator`` CLI). This script substitutes a structural check
for the config families Sprint-027.md's own "Required Tests" section
names:

  promtool check rules    -> validate_prometheus_rules()
  promtool check config   -> validate_prometheus_config()
  amtool check-config     -> validate_alertmanager_config()

Grafana dashboard linting is intentionally NOT duplicated here -- Sprint-016
already built ``scripts/validate_grafana_dashboards.py`` for exactly that
purpose; run it against each dashboard directory instead (it does not
recurse into subdirectories by design):

  python scripts/validate_grafana_dashboards.py --dir monitoring/grafana/dashboards
  python scripts/validate_grafana_dashboards.py --dir monitoring/grafana/dashboards/governance
  python scripts/validate_grafana_dashboards.py --dir monitoring/grafana/dashboards/security

It is not a full PromQL parser -- it checks the structural invariants a
real tool would catch first (valid YAML, required keys present, every
rule has exactly one of record/alert plus a non-empty expr, alertmanager
route/receiver names cross-reference). A real ``promtool``/``amtool`` run
remains the authoritative gate once available.

Usage:
    python scripts/validate_observability_configs.py
Exit code 0 on success, 1 on any structural violation found.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PROM_DIR = REPO_ROOT / "monitoring" / "prometheus"
GRAFANA_DIR = REPO_ROOT / "monitoring" / "grafana"

errors: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def validate_prometheus_config() -> None:
    """Structural analogue of `promtool check config prometheus.yml`."""
    path = PROM_DIR / "prometheus.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if "global" not in data:
        fail(f"{path}: missing top-level 'global'")
    if "scrape_configs" not in data or not isinstance(data["scrape_configs"], list) or not data["scrape_configs"]:
        fail(f"{path}: missing/empty 'scrape_configs'")
    job_names = [sc.get("job_name") for sc in data.get("scrape_configs", [])]
    if len(job_names) != len(set(job_names)):
        fail(f"{path}: duplicate job_name values found")
    for sc in data.get("scrape_configs", []):
        if "job_name" not in sc:
            fail(f"{path}: a scrape_config is missing 'job_name'")
        has_target_source = any(k in sc for k in ("static_configs", "kubernetes_sd_configs", "file_sd_configs"))
        if not has_target_source:
            fail(f"{path}: scrape_config '{sc.get('job_name')}' has no target source")
    for rule_file in data.get("rule_files", []):
        if not (PROM_DIR / rule_file).exists():
            fail(f"{path}: referenced rule_file '{rule_file}' does not exist")
    print(f"OK   prometheus config: {path.relative_to(REPO_ROOT)} ({len(job_names)} scrape jobs)")


def validate_prometheus_rules() -> None:
    """Structural analogue of `promtool check rules`."""
    rule_files = [PROM_DIR / "recording_rules.yml", *sorted((PROM_DIR / "alert_rules").glob("*.yml"))]
    total_rules = 0
    for path in rule_files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if "groups" not in data or not isinstance(data["groups"], list) or not data["groups"]:
            fail(f"{path}: missing/empty 'groups'")
            continue
        group_names = [g.get("name") for g in data["groups"]]
        if len(group_names) != len(set(group_names)):
            fail(f"{path}: duplicate group names")
        for group in data["groups"]:
            if "name" not in group:
                fail(f"{path}: a group is missing 'name'")
            rules = group.get("rules", [])
            if not rules:
                fail(f"{path}: group '{group.get('name')}' has no rules")
            for rule in rules:
                total_rules += 1
                has_record = "record" in rule
                has_alert = "alert" in rule
                if has_record == has_alert:
                    fail(f"{path}: rule in group '{group.get('name')}' must have exactly one of record/alert: {rule}")
                if not rule.get("expr"):
                    fail(f"{path}: rule '{rule.get('record') or rule.get('alert')}' has empty expr")
                if has_alert:
                    if "labels" not in rule or "severity" not in rule.get("labels", {}):
                        fail(f"{path}: alert '{rule.get('alert')}' missing labels.severity")
                    if "annotations" not in rule or "summary" not in rule.get("annotations", {}):
                        fail(f"{path}: alert '{rule.get('alert')}' missing annotations.summary")
        print(
            f"OK   rule file: {path.relative_to(REPO_ROOT)} ({sum(len(g.get('rules', [])) for g in data['groups'])} rules)"
        )
    print(f"OK   {total_rules} total rules checked across {len(rule_files)} files")


def validate_alertmanager_config() -> None:
    """Structural analogue of `amtool check-config`."""
    path = GRAFANA_DIR / "alerts" / "alertmanager.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if "route" not in data:
        fail(f"{path}: missing top-level 'route'")
    if "receivers" not in data or not data["receivers"]:
        fail(f"{path}: missing/empty 'receivers'")
        return
    receiver_names = {r["name"] for r in data["receivers"]}

    def check_route(route: dict) -> None:
        receiver = route.get("receiver")
        if receiver and receiver not in receiver_names:
            fail(f"{path}: route references undefined receiver '{receiver}'")
        for sub in route.get("routes", []):
            check_route(sub)

    check_route(data["route"])
    for inhibit in data.get("inhibit_rules", []):
        if "source_matchers" not in inhibit or "target_matchers" not in inhibit:
            fail(f"{path}: inhibit_rule missing source_matchers/target_matchers")
    print(f"OK   alertmanager config: {len(receiver_names)} receivers, route tree valid")


def validate_logging_tracing_configs() -> None:
    """Basic parseability check for the logging/tracing YAML configs (no dedicated CLI tool for these)."""
    for rel in ("logging/loki.yml", "tracing/otel-collector.yml", "tracing/jaeger.yml"):
        path = REPO_ROOT / "monitoring" / rel
        yaml.safe_load(path.read_text(encoding="utf-8"))
        print(f"OK   parses: {path.relative_to(REPO_ROOT)}")
    fluentbit_path = REPO_ROOT / "monitoring" / "logging" / "fluentbit.conf"
    text = fluentbit_path.read_text(encoding="utf-8")
    for required_section in ("[SERVICE]", "[INPUT]", "[OUTPUT]"):
        if required_section not in text:
            fail(f"{fluentbit_path}: missing required section {required_section}")
    print(f"OK   parses: {fluentbit_path.relative_to(REPO_ROOT)}")


def main() -> int:
    validate_prometheus_config()
    validate_prometheus_rules()
    validate_alertmanager_config()
    validate_logging_tracing_configs()

    if errors:
        print(f"\n{len(errors)} FAILURE(S):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print("\nAll observability configs structurally valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
