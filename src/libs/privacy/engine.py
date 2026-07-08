"""PrivacyEngine — purpose check + minimization facade (V4 Ch9 §9.7).

Architecture: V4 Ch9 §9.7 (``PrivacyService.check_purpose``), §9.12
("purpose-bound: data used only for consented purposes; secondary use
requires pseudonymization/anonymization or separate consent").
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.libs.privacy.metrics import record_purpose_check
from src.libs.privacy.purpose_registry import DataClass, Purpose, PurposeRegistry


class PrivacyObligation(StrEnum):
    """An action the caller must take given a PrivacyDecision (V4 Ch9 §9.6)."""

    MINIMIZE = "minimize"
    PSEUDONYMIZE = "pseudonymize"
    RETAIN_UNTIL = "retain_until"
    ERASE = "erase"


class PrivacyDecision(BaseModel):
    """Whether processing ``data_class`` for ``purpose`` is allowed, plus any obligations."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    purpose: Purpose
    obligations: tuple[PrivacyObligation, ...] = ()


class PrivacyEngine:
    """Checks purpose-limitation + consent before data is used for a given purpose."""

    def __init__(self, purpose_registry: PurposeRegistry, audit_repository: Any | None = None) -> None:
        self._purpose_registry = purpose_registry
        self._audit_repository = audit_repository

    def check_purpose(
        self,
        data_class: DataClass,
        purpose: Purpose,
        *,
        consent_granted: bool,
        tenant_id: str | None = None,
        actor_id: str = "system",
    ) -> PrivacyDecision:
        """Check ``purpose ∈ consented_purposes`` for ``data_class`` (V4 Ch9 §9.12).

        ``consent_granted`` reflects the caller's own consent lookup (e.g.
        ``ConsentRepository.check_consent(...).status == GRANTED``) — this
        engine does not query consent state itself, matching PolicyEngine's
        precedent of taking pre-resolved facts rather than owning every
        upstream lookup.
        """
        purpose_allowed = self._purpose_registry.is_allowed(data_class, purpose)
        allowed = consent_granted and purpose_allowed

        obligations: tuple[PrivacyObligation, ...] = ()
        if not purpose_allowed and consent_granted:
            obligations = (PrivacyObligation.PSEUDONYMIZE,)

        decision = PrivacyDecision(allowed=allowed, purpose=purpose, obligations=obligations)
        record_purpose_check(allowed)

        if self._audit_repository is not None and tenant_id is not None:
            self._audit_repository.append(
                tenant_id,
                actor_id=actor_id,
                action="privacy.check_purpose",
                resource_type=data_class.value,
                resource_id=purpose.value,
                outcome="allowed" if allowed else "denied",
            )
        return decision
