"""Persistent data model for PlatformUser -- VoiceOS's own staff (ADR-005 Sec 3/4.2).

Deliberately has no ``tenant_id`` and is never composed with the tenant-scoped
``User``/``Role``/``RoleAssignment`` models in ``user.py`` -- a PlatformActor
is a structurally separate identity, not a ``User`` with ``tenant_id=None``.

Architecture: ADR-005 Sec 3 (Actor Model), Sec 4.2 (New Components).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PlatformUser(BaseModel):
    """A VoiceOS platform-owner staff account (ADR-005 Sec 3)."""

    model_config = ConfigDict(frozen=True)

    platform_user_id: str = Field(min_length=1)
    email: str = Field(min_length=5, max_length=254)
    name: str = Field(min_length=1, max_length=200)
    platform_role: str
    """One of PlatformRole's values (src.services.platform_admin.roles) --
    kept as ``str`` here, the same way ``User`` keeps role assignments as
    plain data, so this contract model has no import dependency on the
    service-layer enum."""
    is_active: bool = True
    mfa_enabled: bool = False
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


__all__ = ["PlatformUser"]
