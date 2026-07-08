"""Unit tests for the secrets scanner (scripts/check_secrets.py).

Verifies the regex layer catches a planted hardcoded secret and passes on a
clean file. Mirrors ``tests/unit/test_boundary_checker.py``'s
subprocess-invocation pattern.

Architecture: V4 Ch7 §7.12 (secrets scanning); V6 Ch9 (Testing Standards).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCANNER = Path(__file__).parent.parent.parent / "scripts" / "check_secrets.py"


def _run(*paths: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCANNER), *paths],
        capture_output=True,
        text=True,
        check=False,
    )


def test_secrets_scan_catches_hardcoded(tmp_path: Path) -> None:
    """Plant a fake secret in a temp file — the scanner must find it."""
    planted = tmp_path / "config.py"
    planted.write_text('AWS_ACCESS_KEY_ID = "AKIAABCDEFGHIJKLMNOP"\n')  # pragma: allowlist secret

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "aws_access_key_id" in result.stderr


def test_clean_file_passes(tmp_path: Path) -> None:
    clean = tmp_path / "clean.py"
    clean.write_text("def add(a: int, b: int) -> int:\n    return a + b\n")

    result = _run(str(tmp_path))

    assert result.returncode == 0


def test_allowlisted_placeholder_does_not_trigger(tmp_path: Path) -> None:
    placeholder = tmp_path / ".env.example"
    placeholder.write_text('API_KEY="CHANGE_ME"\n')

    result = _run(str(tmp_path))

    assert result.returncode == 0


def test_missing_path_exits_nonzero() -> None:
    result = _run("/does/not/exist")

    assert result.returncode == 1
