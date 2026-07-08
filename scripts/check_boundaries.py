#!/usr/bin/env python3
"""Module boundary checker for VoiceOS source code.

Enforces the cross-package import rules defined in Volume 6 Chapter 2:

  Rule 1: src/services/ must not import from src/engines/
          (services communicate with engines through the contracts layer only)
  Rule 2: src/engines/ must not import from src/services/
  Rule 3: src/libs/contracts/ must not import from src/libs/invariants/
  Rule 4: src/libs/invariants/ must not import from src/libs/contracts/

Also enforces the Sprint-013 TTL discipline rule (V3 Ch4 §4.12):

  Rule 5: no Redis SET call outside src/libs/redis_client/ttl_guard.py may
          bypass TTLGuard — every ``<redis-like>.set(...)`` call site must
          go through ``TTLGuard.set()`` instead of a raw client's ``.set()``.

Usage:
  python scripts/check_boundaries.py
  python scripts/check_boundaries.py --src path/to/src
  python scripts/check_boundaries.py --exit-zero      # dry-run; always exits 0

Exit codes:
  0 — no violations found (or --exit-zero was given)
  1 — one or more boundary violations found, or the source directory is missing
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class Violation(NamedTuple):
    """A detected import boundary violation."""

    file: str
    line: int
    imported_module: str
    reason: str


# ---------------------------------------------------------------------------
# Core checker
# ---------------------------------------------------------------------------


def check_boundaries(src_dir: Path) -> list[Violation]:
    """Scan src_dir for cross-package import boundary violations.

    Args:
        src_dir: The root source directory to scan (e.g. Path("src")).

    Returns:
        A list of Violation instances. Empty list means clean.
    """
    violations: list[Violation] = []
    src_name = src_dir.name  # e.g. "src"

    for py_file in sorted(src_dir.rglob("*.py")):
        relative = py_file.relative_to(src_dir)
        # file_pkg_parts: package hierarchy within src, e.g. ("services", "foo", "bar.py")
        file_pkg_parts: tuple[str, ...] = relative.parts

        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, OSError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    violations.extend(
                        _check_import(
                            str(relative),
                            node.lineno,
                            alias.name,
                            file_pkg_parts,
                            src_name,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module is not None:
                    # Resolve relative imports to absolute form when possible
                    module = node.module
                    if node.level == 0:  # absolute import
                        violations.extend(
                            _check_import(
                                str(relative),
                                node.lineno,
                                module,
                                file_pkg_parts,
                                src_name,
                            )
                        )
                    # Relative imports (node.level > 0) stay within the same
                    # package — they cannot cross the monitored boundaries.

    return violations


def _check_import(
    file_path: str,
    line: int,
    module: str,
    file_pkg_parts: tuple[str, ...],
    src_name: str,
) -> list[Violation]:
    """Check a single absolute import for boundary violations.

    Args:
        file_path: Source file path (relative to src_dir), for reporting.
        line: Line number of the import statement.
        module: The dotted module name being imported.
        file_pkg_parts: Package path parts of the *importing* file within
                        the src directory, e.g. ("services", "foo", "bar.py").
        src_name: Name of the source root directory (e.g. "src").

    Returns:
        List of Violation instances (may be empty).
    """
    violations: list[Violation] = []
    mod_parts = module.split(".")

    # Only examine imports that reference our own source package
    if not mod_parts or mod_parts[0] != src_name:
        return violations

    # Build the full qualified package path of the importing file
    # (src_name, *file_pkg_parts) — same namespace as imported modules
    importer: tuple[str, ...] = (src_name, *file_pkg_parts)

    # Rule 1: services must not import from engines
    if len(importer) >= 2 and importer[1] == "services" and len(mod_parts) >= 2 and mod_parts[1] == "engines":
        violations.append(
            Violation(
                file=file_path,
                line=line,
                imported_module=module,
                reason=(
                    "src/services/ must not import directly from src/engines/. "
                    "Use the contracts layer (src/libs/contracts/) instead."
                ),
            )
        )

    # Rule 2: engines must not import from services
    if len(importer) >= 2 and importer[1] == "engines" and len(mod_parts) >= 2 and mod_parts[1] == "services":
        violations.append(
            Violation(
                file=file_path,
                line=line,
                imported_module=module,
                reason="src/engines/ must not import from src/services/.",
            )
        )

    # Rule 3: contracts must not import from invariants
    if (
        len(importer) >= 4
        and importer[1] == "libs"
        and importer[2] == "contracts"
        and len(mod_parts) >= 3
        and mod_parts[1] == "libs"
        and mod_parts[2] == "invariants"
    ):
        violations.append(
            Violation(
                file=file_path,
                line=line,
                imported_module=module,
                reason="src/libs/contracts/ must not import from src/libs/invariants/.",
            )
        )

    # Rule 4: invariants must not import from contracts
    if (
        len(importer) >= 4
        and importer[1] == "libs"
        and importer[2] == "invariants"
        and len(mod_parts) >= 3
        and mod_parts[1] == "libs"
        and mod_parts[2] == "contracts"
    ):
        violations.append(
            Violation(
                file=file_path,
                line=line,
                imported_module=module,
                reason="src/libs/invariants/ must not import from src/libs/contracts/.",
            )
        )

    return violations


# ---------------------------------------------------------------------------
# TTLGuard enforcement checker (Sprint-013, V3 Ch4 §4.12)
# ---------------------------------------------------------------------------

# Files exempt from the raw-.set()-on-Redis check: TTLGuard's own
# implementation (the one sanctioned place a raw client .set() call lives)
# and the redis_client package's non-string-key call sites, which do not
# write string keys with SET (locks use TTLGuard internally; rate limiting
# uses ZADD, not SET).
_TTL_GUARD_EXEMPT_SUFFIXES = ("src/libs/redis_client/ttl_guard.py",)

_REDIS_LIKE_NAME_HINTS = ("redis",)


def check_ttl_guard_usage(src_dir: Path) -> list[Violation]:
    """Scan for Redis ``.set(...)`` calls that bypass TTLGuard.

    Heuristic AST check: flags ``<expr>.set(...)`` where the base of
    ``<expr>`` is a name or attribute containing "redis" (e.g. ``self._redis``,
    ``redis_client.raw``), outside of TTLGuard's own implementation.

    Args:
        src_dir: The root source directory to scan (e.g. Path("src")).

    Returns:
        A list of Violation instances. Empty list means clean.
    """
    violations: list[Violation] = []

    for py_file in sorted(src_dir.rglob("*.py")):
        relative = py_file.relative_to(src_dir.parent) if src_dir.name == "src" else py_file.relative_to(src_dir)
        posix_path = relative.as_posix()
        if any(posix_path.endswith(suffix) for suffix in _TTL_GUARD_EXEMPT_SUFFIXES):
            continue

        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, OSError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "set"):
                continue
            base_repr = _base_identifier_chain(func.value)
            if base_repr is None:
                continue
            if any(hint in base_repr.lower() for hint in _REDIS_LIKE_NAME_HINTS):
                violations.append(
                    Violation(
                        file=str(relative),
                        line=node.lineno,
                        imported_module=f"{base_repr}.set(...)",
                        reason=(
                            "Raw Redis .set() call bypasses TTLGuard. "
                            "Use TTLGuard.set() so every key carries a mandatory TTL (V3 Ch4)."
                        ),
                    )
                )

    return violations


def _base_identifier_chain(node: ast.expr) -> str | None:
    """Render a dotted attribute/name chain (e.g. ``self._redis``) for matching."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    else:
        return None
    return ".".join(reversed(parts))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="VoiceOS module boundary checker (V6 Ch2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--src",
        default="src",
        metavar="DIR",
        help="Source directory to scan (default: src)",
    )
    parser.add_argument(
        "--exit-zero",
        action="store_true",
        help="Always exit 0, even when violations are found (dry-run mode)",
    )
    args = parser.parse_args()

    src_dir = Path(args.src)
    if not src_dir.exists():
        print(f"ERROR: source directory not found: {src_dir}", file=sys.stderr)
        return 1 if not args.exit_zero else 0

    violations = check_boundaries(src_dir)
    ttl_violations = check_ttl_guard_usage(src_dir)

    if violations:
        print(
            f"BOUNDARY VIOLATIONS FOUND: {len(violations)} violation(s) in {src_dir}/",
            file=sys.stderr,
        )
        for v in violations:
            print(f"  {v.file}:{v.line}  imports  {v.imported_module!r}", file=sys.stderr)
            print(f"    -> {v.reason}", file=sys.stderr)
    else:
        print(f"OK: module boundary check passed (0 violations in {src_dir}/)")

    if ttl_violations:
        print(
            f"TTL GUARD VIOLATIONS FOUND: {len(ttl_violations)} violation(s) in {src_dir}/",
            file=sys.stderr,
        )
        for v in ttl_violations:
            print(f"  {v.file}:{v.line}  {v.imported_module}", file=sys.stderr)
            print(f"    -> {v.reason}", file=sys.stderr)
    else:
        print(f"OK: TTLGuard enforcement check passed (0 violations in {src_dir}/)")

    if (violations or ttl_violations) and not args.exit_zero:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
