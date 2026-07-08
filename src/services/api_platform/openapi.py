"""OpenAPI 3.1 schema loading -- spec-first Public API (V5 Ch16, Sprint-025.md).

``api-specs/voiceos-public-v1.yaml`` is the single source of truth (spec
authored first, served verbatim) -- this module only loads and parses it,
it never generates or duplicates the schema in Python.

Architecture: V5 Ch16 (API Platform -- spec-first OpenAPI 3.1).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEFAULT_SPEC_PATH = Path(__file__).resolve().parents[3] / "api-specs" / "voiceos-public-v1.yaml"


@lru_cache(maxsize=1)
def get_openapi_schema(spec_path: Path = DEFAULT_SPEC_PATH) -> dict[str, Any]:
    """Load and parse the OpenAPI 3.1 spec file, cached after first read."""
    with spec_path.open("r", encoding="utf-8") as fh:
        schema: dict[str, Any] = yaml.safe_load(fh)
    return schema


__all__ = ["DEFAULT_SPEC_PATH", "get_openapi_schema"]
