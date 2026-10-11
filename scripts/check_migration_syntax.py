"""Validate that all Alembic migration files are valid Python syntax."""
from __future__ import annotations

import ast
import pathlib
import sys

vdir = pathlib.Path("scripts/db/migrations/alembic/versions")
errors: list[str] = []
files = sorted(vdir.glob("*.py"))
for f in files:
    try:
        ast.parse(f.read_text())
    except SyntaxError as e:
        errors.append(f"{f}: {e}")

if errors:
    for e in errors:
        print(e)
    sys.exit(1)

print(f"All {len(files)} migration files are valid Python")
