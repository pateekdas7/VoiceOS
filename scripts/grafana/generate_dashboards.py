#!/usr/bin/env python3
"""Generate VoiceOS v2's Grafana dashboard JSON files (Sprint-027).

Every dashboard in monitoring/grafana/dashboards/ (and its governance/ and
security/ subdirectories) is generated from the declarative panel specs
below rather than hand-typed, the same "one canonical template, many
generated artifacts" precedent as infra/helm's
scripts/helm/generate_service_charts.sh (Sprint-026) -- it guarantees every
dashboard is structurally identical (valid schemaVersion, panel id
uniqueness, a Prometheus datasource variable) and keeps 15 dashboards
consistent without 15 hand-maintained JSON files drifting apart.

Usage:
    python scripts/grafana/generate_dashboards.py
    python scripts/grafana/generate_dashboards.py --check   # verify up to date, exit 1 if not

Architecture: V7 Ch7 (Monitoring Platform); V4 Ch18 (Governance Dashboards);
V4 Ch23 (Security Metrics & KPIs).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARDS_DIR = REPO_ROOT / "monitoring" / "grafana" / "dashboards"

_next_panel_id = 1


def _panel_id() -> int:
    global _next_panel_id
    pid = _next_panel_id
    _next_panel_id += 1
    return pid


def _target(expr: str, legend: str = "") -> dict[str, Any]:
    return {
        "expr": expr,
        "legendFormat": legend,
        "refId": "A",
        "datasource": {"type": "prometheus", "uid": "${datasource}"},
    }


def _panel(
    title: str,
    kind: str,
    exprs: list[tuple[str, str]],
    *,
    x: int,
    y: int,
    w: int = 12,
    h: int = 8,
    unit: str = "short",
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    targets = []
    for i, (expr, legend) in enumerate(exprs):
        t = _target(expr, legend)
        t["refId"] = chr(ord("A") + i)
        targets.append(t)
    panel: dict[str, Any] = {
        "id": _panel_id(),
        "title": title,
        "type": kind,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": {"type": "prometheus", "uid": "${datasource}"},
        "targets": targets,
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "options": {},
    }
    if thresholds:
        panel["fieldConfig"]["defaults"]["thresholds"] = thresholds
    return panel


def _dashboard(uid: str, title: str, description: str, panels: list[dict[str, Any]], tags: list[str]) -> dict[str, Any]:
    return {
        "id": None,
        "uid": uid,
        "title": title,
        "description": description,
        "tags": tags,
        "schemaVersion": 39,
        "version": 1,
        "editable": True,
        "graphTooltip": 1,
        "timezone": "utc",
        "time": {"from": "now-6h", "to": "now"},
        "refresh": "30s",
        "templating": {
            "list": [
                {
                    "name": "datasource",
                    "type": "datasource",
                    "query": "prometheus",
                    "current": {},
                    "label": "Datasource",
                }
            ]
        },
        "annotations": {"list": []},
        "panels": panels,
    }


def _write(
    uid: str, title: str, description: str, panels: list[dict[str, Any]], tags: list[str], rel_path: str
) -> None:
    global _next_panel_id
    _next_panel_id = 1
    dashboard = _dashboard(uid, title, description, panels, tags)
    out_path = DASHBOARDS_DIR / rel_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dashboard, indent=2, sort_keys=False) + "\n"
    out_path.write_text(text, encoding="utf-8")
    try:
        print(f"wrote {out_path.relative_to(REPO_ROOT)}")
    except ValueError:
        print(f"wrote {out_path}")


def build_slo_overview() -> None:
    panels = [
        _panel(
            "First-Audio p95 (SLO <= 1.5s)",
            "timeseries",
            [("voiceos:first_audio_seconds:p95_5m", "p95 5m"), ("voiceos:first_audio_seconds:p95_1h", "p95 1h")],
            x=0,
            y=0,
            unit="s",
        ),
        _panel(
            "Availability (SLO >= 99.95%)",
            "timeseries",
            [("voiceos:availability:ratio_1h * 100", "availability % (1h)")],
            x=12,
            y=0,
            unit="percent",
        ),
        _panel(
            "First-Audio Error Budget Burn Rate",
            "timeseries",
            [
                ("voiceos:first_audio_slo:burn_rate_5m", "burn 5m"),
                ("voiceos:first_audio_slo:burn_rate_1h", "burn 1h"),
                ("voiceos:first_audio_slo:burn_rate_6h", "burn 6h"),
            ],
            x=0,
            y=8,
            unit="short",
        ),
        _panel(
            "Availability Error Budget Burn Rate",
            "timeseries",
            [
                ("voiceos:availability_slo:burn_rate_5m", "burn 5m"),
                ("voiceos:availability_slo:burn_rate_1h", "burn 1h"),
                ("voiceos:availability_slo:burn_rate_6h", "burn 6h"),
            ],
            x=12,
            y=8,
            unit="short",
        ),
        _panel(
            "Error Budget Remaining (30d, first-audio)",
            "gauge",
            [("1 - voiceos:first_audio_slo:burn_rate_6h / 6", "budget remaining")],
            x=0,
            y=16,
            w=24,
            unit="percentunit",
            thresholds={
                "mode": "absolute",
                "steps": [
                    {"color": "red", "value": None},
                    {"color": "yellow", "value": 0.2},
                    {"color": "green", "value": 0.5},
                ],
            },
        ),
    ]
    _write(
        "voiceos-slo-overview",
        "VoiceOS / SLO Overview",
        "SLO attainment and error-budget burn rate, by service.",
        panels,
        ["voiceos", "slo"],
        "slo-overview.json",
    )


def build_call_funnel() -> None:
    stages = ["calls_started", "vad_detected", "stt_transcribed", "cil_decided", "tts_synthesized", "calls_completed"]
    exprs = [(f'sum(increase(voiceos_call_funnel_stage_total{{stage="{s}"}}[1h]))', s) for s in stages]
    panels = [
        _panel(
            "Call Funnel: Connect -> VAD -> STT -> CIL -> TTS -> Completed", "barchart", exprs, x=0, y=0, w=24, h=10
        ),
        _panel(
            "Funnel Drop-off Rate by Stage",
            "timeseries",
            [
                (
                    f'1 - (sum(increase(voiceos_call_funnel_stage_total{{stage="{stages[i + 1]}"}}[1h])) / sum(increase(voiceos_call_funnel_stage_total{{stage="{stages[i]}"}}[1h])))',
                    f"{stages[i]}->{stages[i + 1]}",
                )
                for i in range(len(stages) - 1)
            ],
            x=0,
            y=10,
            w=24,
            unit="percentunit",
        ),
    ]
    _write(
        "voiceos-call-funnel",
        "VoiceOS / Call Funnel",
        "Calls started through VAD, STT, CIL, TTS to completion.",
        panels,
        ["voiceos", "call-funnel"],
        "call-funnel.json",
    )


def build_gpu_fleet() -> None:
    panels = [
        _panel(
            "Fleet Health Score",
            "gauge",
            [("voiceos_gpu_fleet_health_score", "fleet_health_score")],
            x=0,
            y=0,
            unit="percentunit",
            thresholds={
                "mode": "absolute",
                "steps": [
                    {"color": "red", "value": None},
                    {"color": "yellow", "value": 0.5},
                    {"color": "green", "value": 0.8},
                ],
            },
        ),
        _panel(
            "VRAM Used / Available per GPU",
            "timeseries",
            [("voiceos_gpu_vram_used_mb", "used MB"), ("voiceos_gpu_vram_total_mb", "total MB")],
            x=12,
            y=0,
            unit="decmbytes",
        ),
        _panel(
            "Fleet VRAM Budget (aggregate)",
            "timeseries",
            [("voiceos_gpu_fleet_vram_used_mb", "fleet used"), ("voiceos_gpu_fleet_vram_budget_mb", "fleet budget")],
            x=0,
            y=8,
            unit="decmbytes",
        ),
        _panel(
            "Model Pool Occupancy",
            "timeseries",
            [("voiceos_gpu_model_pool_occupancy_ratio", "{{pool}}")],
            x=12,
            y=8,
            unit="percentunit",
        ),
        _panel(
            "Admission Rate",
            "timeseries",
            [
                ("rate(voiceos_gpu_scheduler_admissions_total[5m])", "admitted"),
                ("rate(voiceos_gpu_scheduler_admission_rejections_total[5m])", "rejected"),
            ],
            x=0,
            y=16,
        ),
        _panel("Node Status", "table", [("voiceos_gpu_fleet_node_healthy", "{{node_id}}")], x=12, y=16),
    ]
    _write(
        "voiceos-gpu-fleet",
        "VoiceOS / GPU Fleet",
        "VRAM used/available per GPU, model pool occupancy, admission rate, fleet health.",
        panels,
        ["voiceos", "gpu"],
        "gpu-fleet.json",
    )


def build_latency_breakdown() -> None:
    stages = ["media_gateway", "audio_session_manager", "preprocessing", "vad", "stt", "cil", "llm", "tts", "playback"]
    panels = []
    for i, s in enumerate(stages):
        panels.append(
            _panel(
                f"{s} latency (p50/p95/p99)",
                "timeseries",
                [
                    (f"histogram_quantile(0.50, sum(rate(voiceos_{s}_request_duration_ms_bucket[5m])) by (le))", "p50"),
                    (f"histogram_quantile(0.95, sum(rate(voiceos_{s}_request_duration_ms_bucket[5m])) by (le))", "p95"),
                    (f"histogram_quantile(0.99, sum(rate(voiceos_{s}_request_duration_ms_bucket[5m])) by (le))", "p99"),
                ],
                x=(i % 2) * 12,
                y=(i // 2) * 8,
                unit="ms",
            )
        )
    _write(
        "voiceos-latency-breakdown",
        "VoiceOS / Latency Breakdown",
        "Per-stage p50/p95/p99 latency across the full call pipeline.",
        panels,
        ["voiceos", "latency"],
        "latency-breakdown.json",
    )


def build_reliability() -> None:
    panels = [
        _panel(
            "Circuit Breaker State (0=CLOSED,1=HALF_OPEN,2=OPEN)",
            "timeseries",
            [("voiceos_circuit_breaker_state", "{{service}}")],
            x=0,
            y=0,
        ),
        _panel(
            "DLQ Depth",
            "timeseries",
            [('voiceos_queue_size_current{queue_name=~".*dlq.*"}', "{{queue_name}}")],
            x=12,
            y=0,
        ),
        _panel(
            "Queue Depth vs Max",
            "timeseries",
            [
                ("voiceos_queue_size_current", "{{queue_name}} current"),
                ("voiceos_queue_size_max", "{{queue_name}} max"),
            ],
            x=0,
            y=8,
        ),
        _panel("Retry Rate", "timeseries", [("rate(voiceos_retry_attempts_total[5m])", "{{service}}")], x=12, y=8),
        _panel(
            "Recovery Events",
            "timeseries",
            [("increase(voiceos_recovery_events_total[1h])", "{{strategy}}")],
            x=0,
            y=16,
            w=24,
        ),
    ]
    _write(
        "voiceos-reliability",
        "VoiceOS / Reliability",
        "Circuit breaker states, DLQ depth, retry rates, recovery events.",
        panels,
        ["voiceos", "reliability"],
        "reliability.json",
    )


def build_business() -> None:
    panels = [
        _panel("Calls Today", "stat", [("sum(increase(voiceos_call_count_total[24h]))", "calls")], x=0, y=0, w=8, h=6),
        _panel(
            "PTPs Today",
            "stat",
            [('sum(increase(voiceos_negotiation_outcome_total{outcome="ptp_created"}[24h]))', "ptps")],
            x=8,
            y=0,
            w=8,
            h=6,
        ),
        _panel(
            "Conversion Rate", "stat", [("voiceos:ptp_rate:ratio_1h * 100", "%")], x=16, y=0, w=8, h=6, unit="percent"
        ),
        _panel(
            "Campaign Progress",
            "timeseries",
            [("voiceos:campaign_completion_rate:ratio_1h * 100", "completion %")],
            x=0,
            y=6,
            w=24,
            unit="percent",
        ),
    ]
    _write(
        "voiceos-business",
        "VoiceOS / Business",
        "Calls today, PTPs today, conversion rate, campaign progress.",
        panels,
        ["voiceos", "business"],
        "business.json",
    )


def build_governance() -> None:
    _write(
        "voiceos-gov-consent-coverage",
        "VoiceOS Governance / Consent Coverage",
        "% of calls with valid consent; consent revocation trend.",
        [
            _panel(
                "Consent Coverage %",
                "gauge",
                [("sum(voiceos_calls_with_valid_consent_total) / sum(voiceos_call_count_total) * 100", "coverage")],
                x=0,
                y=0,
                unit="percent",
                thresholds={
                    "mode": "absolute",
                    "steps": [
                        {"color": "red", "value": None},
                        {"color": "yellow", "value": 95},
                        {"color": "green", "value": 99},
                    ],
                },
            ),
            _panel(
                "Consent Revocation Trend",
                "timeseries",
                [("increase(voiceos_consent_revoked_total[1d])", "revocations/day")],
                x=12,
                y=0,
            ),
        ],
        ["voiceos", "governance"],
        "governance/consent-coverage.json",
    )

    _write(
        "voiceos-gov-policy-violations",
        "VoiceOS Governance / Policy Violations",
        "Policy denial rate by domain: RBI, DPDP, Billing.",
        [
            _panel(
                "Policy Denial Rate by Domain",
                "timeseries",
                [('rate(voiceos_policy_decision_total{outcome="deny"}[5m])', "{{domain}}")],
                x=0,
                y=0,
                w=24,
            ),
            _panel(
                "Denials Today by Domain",
                "piechart",
                [('sum by (domain) (increase(voiceos_policy_decision_total{outcome="deny"}[24h]))', "{{domain}}")],
                x=0,
                y=8,
            ),
        ],
        ["voiceos", "governance"],
        "governance/policy-violations.json",
    )

    _write(
        "voiceos-gov-ai-incidents",
        "VoiceOS Governance / AI Incidents",
        "AI misbehavior incidents: BLOCK verdicts, REQUIRE_HUMAN rate.",
        [
            _panel(
                "BLOCK Verdicts",
                "timeseries",
                [('rate(voiceos_ai_governance_verdict_total{verdict="block"}[5m])', "block rate")],
                x=0,
                y=0,
            ),
            _panel(
                "REQUIRE_HUMAN Rate",
                "timeseries",
                [('rate(voiceos_ai_governance_verdict_total{verdict="require_human"}[5m])', "require_human rate")],
                x=12,
                y=0,
            ),
        ],
        ["voiceos", "governance"],
        "governance/ai-incidents.json",
    )

    _write(
        "voiceos-gov-data-retention",
        "VoiceOS Governance / Data Retention",
        "Data older than retention policy: red if any records found.",
        [
            _panel(
                "Records Beyond Retention Policy",
                "stat",
                [("sum(voiceos_data_retention_violations_total)", "violations")],
                x=0,
                y=0,
                w=24,
                h=6,
                thresholds={
                    "mode": "absolute",
                    "steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}],
                },
            ),
        ],
        ["voiceos", "governance"],
        "governance/data-retention.json",
    )

    _write(
        "voiceos-gov-break-glass",
        "VoiceOS Governance / Break-Glass Usage",
        "Supervisor override count, rationale coverage audit.",
        [
            _panel(
                "Break-Glass Override Count",
                "timeseries",
                [("increase(voiceos_break_glass_invocations_total[1d])", "overrides/day")],
                x=0,
                y=0,
            ),
            _panel(
                "Rationale Coverage %",
                "gauge",
                [
                    (
                        "sum(voiceos_break_glass_with_rationale_total) / sum(voiceos_break_glass_invocations_total) * 100",
                        "coverage",
                    )
                ],
                x=12,
                y=0,
                unit="percent",
                thresholds={
                    "mode": "absolute",
                    "steps": [{"color": "red", "value": None}, {"color": "green", "value": 100}],
                },
            ),
        ],
        ["voiceos", "governance"],
        "governance/break-glass-usage.json",
    )


def build_security() -> None:
    _write(
        "voiceos-sec-kpis",
        "VoiceOS Security / Security KPIs",
        "MTTD per violation class, MTTR per incident class.",
        [
            _panel(
                "MTTD by Violation Class",
                "bargauge",
                [("voiceos_security_mttd_seconds", "{{violation_class}}")],
                x=0,
                y=0,
                unit="s",
            ),
            _panel(
                "MTTR by Incident Class",
                "bargauge",
                [("voiceos_security_mttr_seconds", "{{incident_class}}")],
                x=12,
                y=0,
                unit="s",
            ),
        ],
        ["voiceos", "security"],
        "security/security-kpis.json",
    )

    _write(
        "voiceos-sec-vulnerabilities",
        "VoiceOS Security / Vulnerability Tracker",
        "Open vulnerability count by severity, patch lag.",
        [
            _panel(
                "Open Vulnerabilities by Severity",
                "timeseries",
                [("voiceos_open_vulnerabilities_total", "{{severity}}")],
                x=0,
                y=0,
            ),
            _panel(
                "Patch Lag (days)",
                "timeseries",
                [("voiceos_vulnerability_patch_lag_days", "{{severity}}")],
                x=12,
                y=0,
                unit="d",
            ),
        ],
        ["voiceos", "security"],
        "security/vulnerability-tracker.json",
    )

    _write(
        "voiceos-sec-auth-anomalies",
        "VoiceOS Security / Auth Anomalies",
        "Authentication failure rate, MFA bypass attempts.",
        [
            _panel(
                "Authentication Failure Rate by Tenant",
                "timeseries",
                [("rate(voiceos_auth_failures_total[5m])", "{{tenant_id}}")],
                x=0,
                y=0,
                w=24,
            ),
            _panel(
                "MFA Bypass Attempts",
                "timeseries",
                [("increase(voiceos_mfa_bypass_attempts_total[1h])", "attempts/hour")],
                x=0,
                y=8,
                w=24,
            ),
        ],
        ["voiceos", "security"],
        "security/auth-anomalies.json",
    )

    _write(
        "voiceos-sec-threat-detection",
        "VoiceOS Security / Threat Detection",
        "Threat-detection coverage %, active threats.",
        [
            _panel(
                "Threat Detection Coverage %",
                "gauge",
                [("voiceos_threat_detection_coverage_ratio * 100", "coverage")],
                x=0,
                y=0,
                unit="percent",
                thresholds={
                    "mode": "absolute",
                    "steps": [
                        {"color": "red", "value": None},
                        {"color": "yellow", "value": 70},
                        {"color": "green", "value": 90},
                    ],
                },
            ),
            _panel("Active Threats", "stat", [("sum(voiceos_active_threats_total)", "active")], x=12, y=0),
        ],
        ["voiceos", "security"],
        "security/threat-detection.json",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify generated files are up to date; exit 1 if not")
    args = parser.parse_args()

    if args.check:
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            global DASHBOARDS_DIR
            original = DASHBOARDS_DIR
            DASHBOARDS_DIR = tmp_path
            _generate_all()
            DASHBOARDS_DIR = original
            diff = subprocess.run(["diff", "-rq", str(tmp_path), str(original)], capture_output=True, text=True)
            if diff.stdout.strip():
                print(diff.stdout)
                print("Dashboards out of date -- re-run scripts/grafana/generate_dashboards.py", file=sys.stderr)
                return 1
            print("All dashboards up to date.")
            return 0

    _generate_all()
    return 0


def _generate_all() -> None:
    build_slo_overview()
    build_call_funnel()
    build_gpu_fleet()
    build_latency_breakdown()
    build_reliability()
    build_business()
    build_governance()
    build_security()


if __name__ == "__main__":
    sys.exit(main())
