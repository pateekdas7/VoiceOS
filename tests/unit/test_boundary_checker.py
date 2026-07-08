"""Unit tests for the module boundary checker (scripts/check_boundaries.py).

Verifies that the checker detects cross-package import violations and passes
on clean source trees.

Architecture: V6 Ch2 (Module Boundaries); V6 Ch9 (Testing Standards).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

_CHECKER = Path(__file__).parent.parent.parent / "scripts" / "check_boundaries.py"
_PROJECT_ROOT = Path(__file__).parent.parent.parent


def _run(extra_args: list[str] | None = None, src: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run the boundary checker and return the CompletedProcess."""
    cmd = [sys.executable, str(_CHECKER)]
    if src is not None:
        cmd += ["--src", src]
    if extra_args:
        cmd += extra_args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(_PROJECT_ROOT),
    )


def _make_src(tmp_path: Path) -> Path:
    """Create a minimal src/ skeleton under tmp_path."""
    src = tmp_path / "src"
    (src / "services").mkdir(parents=True)
    (src / "engines").mkdir(parents=True)
    (src / "libs" / "contracts").mkdir(parents=True)
    (src / "libs" / "invariants").mkdir(parents=True)
    for d in [
        src,
        src / "services",
        src / "engines",
        src / "libs",
        src / "libs" / "contracts",
        src / "libs" / "invariants",
    ]:
        (d / "__init__.py").write_text("")
    return src


# ---------------------------------------------------------------------------
# Script existence and clean run
# ---------------------------------------------------------------------------


class TestCheckerScript:
    def test_script_exists(self) -> None:
        assert _CHECKER.exists(), f"check_boundaries.py not found at {_CHECKER}"

    def test_real_src_has_no_violations(self) -> None:
        """The actual src/ directory must be clean — 0 boundary violations."""
        result = _run(src="src")
        assert result.returncode == 0, f"Unexpected boundary violations in src/:\n{result.stderr}"

    def test_missing_src_dir_exits_nonzero(self) -> None:
        result = _run(src="__nonexistent_src__")
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# Rule 1: services → engines forbidden
# ---------------------------------------------------------------------------


class TestRule1ServicesCannotImportEngines:
    def test_detects_services_importing_engines(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        (src / "services" / "bad.py").write_text(
            textwrap.dedent("""\
                from src.engines.intent import IntentEngine
            """)
        )
        result = _run(src=str(src))
        assert result.returncode != 0
        assert "engines" in result.stderr

    def test_services_may_import_contracts(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        (src / "services" / "ok.py").write_text("from src.libs.contracts.audio import AudioFrame\n")
        result = _run(src=str(src))
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Rule 2: engines → services forbidden
# ---------------------------------------------------------------------------


class TestRule2EnginesCannotImportServices:
    def test_detects_engines_importing_services(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        (src / "engines" / "bad.py").write_text("from src.services.audio_session import AudioSession\n")
        result = _run(src=str(src))
        assert result.returncode != 0
        assert "services" in result.stderr

    def test_engines_may_import_contracts(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        (src / "engines" / "ok.py").write_text("from src.libs.contracts.response_plan import ResponsePlan\n")
        result = _run(src=str(src))
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Rule 3: contracts → invariants forbidden
# ---------------------------------------------------------------------------


class TestRule3ContractsCannotImportInvariants:
    def test_detects_contracts_importing_invariants(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        (src / "libs" / "contracts" / "bad.py").write_text(
            "from src.libs.invariants.guards import assert_ri1_realtime_purity\n"
        )
        result = _run(src=str(src))
        assert result.returncode != 0
        assert "invariants" in result.stderr


# ---------------------------------------------------------------------------
# Rule 4: invariants → contracts forbidden
# ---------------------------------------------------------------------------


class TestRule4InvariantsCannotImportContracts:
    def test_detects_invariants_importing_contracts(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        (src / "libs" / "invariants" / "bad.py").write_text("from src.libs.contracts.audio import AudioFrame\n")
        result = _run(src=str(src))
        assert result.returncode != 0
        assert "contracts" in result.stderr


# ---------------------------------------------------------------------------
# --exit-zero flag
# ---------------------------------------------------------------------------


class TestExitZeroFlag:
    def test_exit_zero_suppresses_failure(self, tmp_path: Path) -> None:
        """--exit-zero makes checker always exit 0, even with violations."""
        src = _make_src(tmp_path)
        (src / "services" / "bad.py").write_text("from src.engines.foo import Bar\n")
        result = _run(extra_args=["--exit-zero"], src=str(src))
        assert result.returncode == 0

    def test_exit_zero_on_clean_src(self, tmp_path: Path) -> None:
        src = _make_src(tmp_path)
        result = _run(extra_args=["--exit-zero"], src=str(src))
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Relative imports are not flagged
# ---------------------------------------------------------------------------


class TestRelativeImportsAreIgnored:
    def test_relative_import_in_invariants_is_allowed(self, tmp_path: Path) -> None:
        """Relative imports within the same package must not be flagged."""
        src = _make_src(tmp_path)
        (src / "libs" / "invariants" / "guards.py").write_text(
            textwrap.dedent("""\
                from .errors import InvariantViolationError
                class InvariantViolationError(Exception): ...
            """)
        )
        result = _run(src=str(src))
        assert result.returncode == 0
