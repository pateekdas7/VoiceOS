#!/usr/bin/env python3
"""Structural validator for api-specs/voiceos-public-v1.yaml (Sprint-025.md AC).

Substitutes for ``openapi-generator validate`` (a Java CLI not installed in
this environment) -- same tool-substitution precedent as Sprint-017's
rsync-unavailable -> scp swap. Checks the structural invariants that matter
for this repo's purposes: required top-level OpenAPI 3.1 keys are present,
every path/operation has an operationId + at least one response, and every
``$ref`` resolves within the document.

Usage:
    python scripts/validate_openapi.py [path/to/spec.yaml]
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

DEFAULT_SPEC_PATH = Path(__file__).resolve().parents[1] / "api-specs" / "voiceos-public-v1.yaml"


class OpenAPIValidationError(ValueError):
    pass


def _resolve_ref(spec: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise OpenAPIValidationError(f"external $ref not supported: {ref}")
    node: Any = spec
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            raise OpenAPIValidationError(f"unresolvable $ref: {ref}")
        node = node[part]
    return node


def _walk_refs(spec: dict[str, Any], node: Any, errors: list[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            try:
                _resolve_ref(spec, ref)
            except OpenAPIValidationError as exc:
                errors.append(str(exc))
        for value in node.values():
            _walk_refs(spec, value, errors)
    elif isinstance(node, list):
        for item in node:
            _walk_refs(spec, item, errors)


def validate(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    for key in ("openapi", "info", "paths"):
        if key not in spec:
            errors.append(f"missing required top-level key: {key!r}")
    if "openapi" in spec and not str(spec["openapi"]).startswith("3.1"):
        errors.append(f"expected OpenAPI 3.1.x, found {spec['openapi']!r}")
    if "info" in spec:
        for key in ("title", "version"):
            if key not in spec["info"]:
                errors.append(f"info.{key} is required")

    http_methods = {"get", "post", "put", "patch", "delete", "options", "head"}
    for path, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            errors.append(f"path {path!r} must be an object")
            continue
        operations = {m: op for m, op in path_item.items() if m in http_methods}
        if not operations:
            errors.append(f"path {path!r} has no HTTP operations")
        for method, operation in operations.items():
            if "operationId" not in operation:
                errors.append(f"{method.upper()} {path}: missing operationId")
            if "responses" not in operation or not operation["responses"]:
                errors.append(f"{method.upper()} {path}: missing responses")

    _walk_refs(spec, spec, errors)
    return errors


def main(argv: list[str]) -> int:
    spec_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_SPEC_PATH
    with spec_path.open("r", encoding="utf-8") as fh:
        spec = yaml.safe_load(fh)

    errors = validate(spec)
    if errors:
        print(f"OpenAPI spec INVALID ({len(errors)} error(s)):")
        for error in errors:
            print(f"  - {error}")
        return 1

    path_count = len(spec.get("paths", {}))
    print(f"OpenAPI spec valid: {spec_path} ({path_count} paths, openapi={spec.get('openapi')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
