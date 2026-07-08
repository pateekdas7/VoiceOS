#!/usr/bin/env python3
"""check_pii_logs.py — CI gate: StructuredLogger never emits raw PII (Sprint-020, V4 Ch10).

Drives ``StructuredLogger`` with a canary line containing every PII entity
type ``PIIDetector`` recognizes and asserts none of the raw values survive
into the emitted JSON. This is the automated equivalent of Sprint-020.md's
Phase 2 manual check ("Run log scan: grep -E '[0-9]{10}' <log_output> -> 0
raw phone numbers visible"), wired as a blocking CI gate so a future change
that bypasses ``PIIRedactor`` in the logging path is caught immediately
rather than only being caught by a Phase-2 grep on the CPU node.

Usage:
    python scripts/check_pii_logs.py

Exit codes:
  0 — no raw PII found in emitted log lines
  1 — raw PII leaked into a log line (regression in the StructuredLogger/
      PIIRedactor wiring)
"""

from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.libs.observability.logger import StructuredLogger

_CANARY_VALUES = (
    "9876543210",  # PHONE
    "1234 5678 9012",  # AADHAAR
    "ABCDE1234F",  # PAN
)


def main() -> int:
    stream = io.StringIO()
    logger = StructuredLogger("check-pii-logs", stream=stream)

    for value in _CANARY_VALUES:
        logger.info(f"canary log line mentioning {value}", customer_note=f"details: {value}")

    emitted = stream.getvalue()
    leaked = [value for value in _CANARY_VALUES if value in emitted]

    if leaked:
        print("FAIL: raw PII found in StructuredLogger output:")
        for value in leaked:
            print(f"  - {value!r}")
        return 1

    print(f"PASS: 0 raw PII values found across {len(_CANARY_VALUES)} canary log lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
