#!/usr/bin/env python3
"""One-shot bootstrap (Phase 6 / D1): add `runbook_url` to every alert.

Mirror of the enforcement script (``check_runbook_coverage.py``) that
*writes* the annotation + stub runbook files instead of merely checking.
Run once to seed coverage; the CI check keeps it from regressing.

- For each alert in ``monitoring/prometheus/alert_rules/*.yml`` that
  lacks ``annotations.runbook_url``, insert one pointing at
  ``docs/runbooks/alerts/<kebab-case-alert-name>.md``.
- Create any missing stub runbook with a short template referencing the
  alert, impact, immediate triage, and link to the dashboard.

Idempotent: re-running makes no changes once coverage is 100%.
"""
from __future__ import annotations

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ALERT_DIR = REPO_ROOT / "monitoring" / "prometheus" / "alert_rules"
RUNBOOK_DIR = REPO_ROOT / "docs" / "runbooks" / "alerts"

ALERT_RE = re.compile(r"^(?P<indent> *)- alert: (?P<name>\w+)\s*$")
ANNOTATIONS_RE = re.compile(r"^(?P<indent> *)annotations:\s*$")
RUNBOOK_RE = re.compile(r"^\s*runbook_url:")


def kebab(name: str) -> str:
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1-\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", s)
    return s.lower()


STUB = """# Runbook: {alert}

**Alert source:** `monitoring/prometheus/alert_rules/*.yml` — alert `{alert}`.

## Impact

Fill in the user-visible impact of this alert firing (e.g. "collection
calls fail to connect", "campaign metrics stale", "DPDP notification
window missed"). The impact drives whether this is a page or a ticket.

## Immediate triage (first 5 minutes)

1. Open the relevant Grafana dashboard — see
   `monitoring/grafana/dashboards/` for the full list.
2. Check `/healthz` on each affected service — a hard failure here means
   the underlying process is unhealthy, not just the metric.
3. Correlate with Loki: filter by `service=<service>` and
   `app_level=~"warn|error|fatal"` over the alert window.
4. If this is a `severity: critical` alert, start an incident in the
   admin portal (`/admin/incidents`) and attach the alert fingerprint.

## Known fixes

- *Fill in as the team learns.* The point of the runbook is to be the
  shortest path from pager → resolution. Document causes you've already
  seen in production, not hypotheticals.

## Related

- SLO: *link the SLO this alert protects, if any.*
- Dashboard: `monitoring/grafana/dashboards/<relevant>.json`
- Dependent services: *list from `MONITORING_DEPENDENCY_MAP.md`*.
"""


def ensure_runbook(name: str) -> pathlib.Path:
    RUNBOOK_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNBOOK_DIR / f"{kebab(name)}.md"
    if not path.exists():
        path.write_text(STUB.format(alert=name), encoding="utf-8")
        print(f"created {path.relative_to(REPO_ROOT)}")
    return path


def patch_yaml(yml: pathlib.Path) -> int:
    text = yml.read_text()
    lines = text.splitlines(keepends=False)
    out: list[str] = []
    changed = 0

    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = ALERT_RE.match(line)
        if not m:
            i += 1
            continue
        alert_name = m.group("name")
        # Scan forward within this rule for `annotations:` and existing runbook_url.
        j = i + 1
        annotations_idx: int | None = None
        has_runbook = False
        while j < len(lines):
            # Stop when we hit the next `- alert:` or dedent to group-level.
            nxt = lines[j]
            if ALERT_RE.match(nxt):
                break
            if nxt.strip().startswith("- ") and not nxt.lstrip().startswith("- "):
                break
            am = ANNOTATIONS_RE.match(nxt)
            if am and annotations_idx is None:
                annotations_idx = len(out) + (j - i - 1)
            if RUNBOOK_RE.match(nxt):
                has_runbook = True
            j += 1

        if not has_runbook:
            path = ensure_runbook(alert_name)
            rel = path.relative_to(REPO_ROOT).as_posix()
            # Append remaining rule lines up to the next alert, locating
            # `annotations:` on the way, and compute the child indent from
            # its indent so we match whichever style the file uses.
            inserted = False
            while i + 1 < len(lines):
                i += 1
                nxt = lines[i]
                if ALERT_RE.match(nxt):
                    i -= 1
                    break
                out.append(nxt)
                am = ANNOTATIONS_RE.match(nxt)
                if am and not inserted:
                    child_indent = am.group("indent") + "  "
                    out.append(f"{child_indent}runbook_url: /{rel}")
                    inserted = True
                    changed += 1
            if not inserted:
                # No annotations block existed — build one using the alert's indent + 2.
                alert_indent = m.group("indent")
                out.append(f"{alert_indent}  annotations:")
                out.append(f"{alert_indent}    runbook_url: /{rel}")
                changed += 1
        else:
            # Pass through unchanged lines of this rule.
            while i + 1 < len(lines):
                i += 1
                nxt = lines[i]
                if ALERT_RE.match(nxt):
                    i -= 1
                    break
                out.append(nxt)

        i += 1

    if changed:
        yml.write_text("\n".join(out) + "\n")
        print(f"patched {yml.relative_to(REPO_ROOT)} (+{changed} runbook_url)")
    return changed


def main() -> int:
    if not ALERT_DIR.is_dir():
        print(f"missing {ALERT_DIR}", file=sys.stderr)
        return 2
    total = 0
    for yml in sorted(ALERT_DIR.glob("*.yml")):
        total += patch_yaml(yml)
    print(f"total patched: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
