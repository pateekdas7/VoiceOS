"""InputValidator — strict allow-list schema validation at the API boundary (V4 Ch12 §12.7, §12.12).

Architecture: V4 Ch12 (API Security) §12.12 ("strict schema validation
(reject unknown/oversized/malformed)... allow-list, not block-list").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

DEFAULT_MAX_PAYLOAD_BYTES = 256 * 1024
"""V4 Ch12 §12.13 default ``max_payload_kb: 256``."""


class RequestValidationError(Exception):
    """Raised when a request body fails size or schema validation."""


@dataclass(frozen=True)
class Validated:
    """A request body that passed size + schema validation."""

    data: dict[str, Any]


class InputValidator:
    """Validates raw request bodies against a pydantic schema, allow-list style."""

    def __init__(self, max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES) -> None:
        self._max_payload_bytes = max_payload_bytes

    def validate(self, payload: bytes, schema: type[BaseModel]) -> Validated:
        """Validate ``payload`` against ``schema``.

        Raises:
            RequestValidationError: oversized payload or schema mismatch
                (unknown fields are rejected — pydantic models used as
                schemas here must set ``model_config = ConfigDict(extra="forbid")``).
        """
        if len(payload) > self._max_payload_bytes:
            raise RequestValidationError(
                f"payload of {len(payload)} bytes exceeds the {self._max_payload_bytes}-byte limit"
            )

        try:
            model = schema.model_validate_json(payload)
        except PydanticValidationError as exc:
            raise RequestValidationError(str(exc)) from exc

        return Validated(data=model.model_dump())
