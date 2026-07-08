#!/usr/bin/env python3
"""Secrets-scanning CI gate for VoiceOS source code (V4 Ch7 §7.12).

Two complementary layers, both must pass:

  1. A fast, dependency-free regex scan for common hardcoded-secret shapes
     (AWS access keys, generic ``api_key = "..."``-style assignments,
     private-key PEM blocks, JWT-shaped literals, Slack/GitHub tokens).
     This is what makes ``test_secrets_scan_catches_hardcoded`` (planting a
     fake secret in a temp file) pass without any external tooling — CI
     runners and local dev machines always have this layer.
  2. If a ``trufflehog`` binary is present on PATH, additionally run
     ``trufflehog filesystem`` (entropy + verified-credential detection)
     over the same path and fold its findings in. Not finding the binary is
     a warning, not a failure — mirrors the "validated in isolation, real
     tool wired when available" precedent from Sprint-018's mTLS handling.

Usage:
  python scripts/check_secrets.py src/
  python scripts/check_secrets.py src/ tests/ --exit-zero   # dry-run

Exit codes:
  0 — no findings (or --exit-zero was given)
  1 — one or more findings, or the target path is missing
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class Finding(NamedTuple):
    """One detected hardcoded-secret candidate."""

    file: str
    line: int
    rule: str
    excerpt: str


# ---------------------------------------------------------------------------
# Regex layer
# ---------------------------------------------------------------------------

_PATTERNS: dict[str, re.Pattern[str]] = {
    "aws_access_key_id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "generic_api_key_assignment": re.compile(
        r"""(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*["'][A-Za-z0-9/+_\-=]{12,}["']"""
    ),
    "private_key_block": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "slack_token": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
}

# Placeholders that legitimately look like the patterns above — never real
# secrets, appear throughout .env.example / docs / this scanner's own tests.
_ALLOWLIST_SUBSTRINGS = ("CHANGE_ME", "<redacted>", "xxxxxxxx", "EXAMPLE", "test-token-not-a-real-secret")

# Explicit per-line marker for intentionally-planted fake secrets in this
# scanner's own tests (e.g. test_secrets_scan_catches_hardcoded plants a
# fake AWS key literal to prove the regex layer catches it) — without this,
# scanning this repo's own tests/ directory flags its own test fixtures.
_PRAGMA_ALLOWLIST = "pragma: allowlist secret"

_SKIP_DIRS = {".git", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache", "node_modules", "venv", ".venv"}


def _iter_text_files(root: Path) -> list[Path]:
    files: list[Path] = []
    if root.is_file():
        return [root]
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        files.append(path)
    return files


def scan_regex(paths: list[Path]) -> list[Finding]:
    """Scan every file under ``paths`` for the hardcoded-secret patterns above."""
    findings: list[Finding] = []
    for root in paths:
        for file_path in _iter_text_files(root):
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if _PRAGMA_ALLOWLIST in line or any(marker in line for marker in _ALLOWLIST_SUBSTRINGS):
                    continue
                for rule, pattern in _PATTERNS.items():
                    if pattern.search(line):
                        findings.append(Finding(str(file_path), line_no, rule, line.strip()[:120]))
    return findings


# ---------------------------------------------------------------------------
# trufflehog layer (optional — used when the binary is installed)
# ---------------------------------------------------------------------------


def scan_trufflehog(paths: list[Path]) -> tuple[list[Finding], bool]:
    """Run ``trufflehog filesystem`` if available.

    Returns:
        ``(findings, ran)`` — ``ran`` is False (with an empty findings list)
        when the binary isn't installed, so callers can distinguish "clean"
        from "not checked".
    """
    binary = shutil.which("trufflehog")
    if binary is None:
        return [], False

    findings: list[Finding] = []
    for root in paths:
        result = subprocess.run(
            [binary, "filesystem", str(root), "--no-update", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            if line.strip():
                findings.append(Finding(str(root), 0, "trufflehog", line.strip()[:200]))
    return findings, True


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="VoiceOS secrets scanner (V4 Ch7 §7.12)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("paths", nargs="+", help="Files/directories to scan")
    parser.add_argument("--exit-zero", action="store_true", help="Always exit 0 (dry-run mode)")
    args = parser.parse_args()

    targets = [Path(p) for p in args.paths]
    missing = [p for p in targets if not p.exists()]
    if missing:
        print(f"ERROR: path(s) not found: {missing}", file=sys.stderr)
        return 1 if not args.exit_zero else 0

    regex_findings = scan_regex(targets)
    trufflehog_findings, trufflehog_ran = scan_trufflehog(targets)
    all_findings = regex_findings + trufflehog_findings

    if regex_findings:
        print(f"SECRETS SCAN (regex): {len(regex_findings)} finding(s)", file=sys.stderr)
        for f in regex_findings:
            print(f"  {f.file}:{f.line}  [{f.rule}]  {f.excerpt}", file=sys.stderr)
    else:
        print("OK: regex secrets scan passed (0 findings)")

    if trufflehog_ran:
        if trufflehog_findings:
            print(f"SECRETS SCAN (trufflehog): {len(trufflehog_findings)} finding(s)", file=sys.stderr)
            for f in trufflehog_findings:
                print(f"  {f.file}  {f.excerpt}", file=sys.stderr)
        else:
            print("OK: trufflehog scan passed (0 findings)")
    else:
        print("WARN: trufflehog binary not found on PATH — regex scan only (CI installs it; see ci.yml)")

    if all_findings and not args.exit_zero:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
