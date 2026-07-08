"""DataMinimizer — field-level allow-list filtering (V4 Ch9 §9.12 "Data minimization").

Architecture: V4 Ch9 §9.12 ("field-level allow-lists at capture; the
pipeline carries only what's needed... 'Don't collect it' is the strongest
privacy control").
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class DataMinimizer:
    """Strips any field not present in an explicit allow-list."""

    def minimize(self, record: Mapping[str, Any], allowed_fields: frozenset[str]) -> dict[str, Any]:
        """Return a copy of ``record`` containing only keys in ``allowed_fields``."""
        return {key: value for key, value in record.items() if key in allowed_fields}

    def allowed_fields_for_consent(self, consented_data_classes: frozenset[str]) -> frozenset[str]:
        """Convenience: consented data-class names double as the allow-list when field
        names match data-class names 1:1 (the common case for simple records)."""
        return consented_data_classes
