"""Sprint-021: tenant lifecycle rework, invitations, sso_config

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-06

``tenants``/``organizations``/``business_units``/``branches``/``users``/
``roles``/``role_assignments`` were all created ahead of schedule in
migrations 0001-0003 (Sprint-014) — but 0001's and 0002's own docstrings
say the ``tenants`` table "is not functionally complete until Sprint-021".
This revision completes it:

- ``tenants.status``: migration 0001 shipped a 5-value CHECK
  (``PROVISIONING``/``ACTIVE``/``SUSPENDED``/``DEPROVISIONING``/``DELETED``)
  that predates Sprint-021's actual lifecycle spec (V5 Ch3): ``TRIAL ->
  SANDBOX -> PRODUCTION -> SUSPENDED -> CANCELLED -> DELETING -> DELETED``.
  This revision maps any existing rows onto the new vocabulary
  (``PROVISIONING``->``TRIAL``, ``ACTIVE``->``PRODUCTION``,
  ``DEPROVISIONING``->``DELETING``, ``SUSPENDED``/``DELETED`` unchanged)
  before swapping the CHECK constraint and column default — no data loss,
  same "complete the partially-built table" precedent as this sprint's own
  charter.
- ``invitations`` (new): the token-based user-invitation workflow
  (Sprint-021.md "invite sent -> token activation -> user created").
- ``sso_config`` (new): a stub table for Sprint-025's real SAML/OIDC SSO
  integration (Sprint-021.md "SSO stub for Sprint-025") — one row per
  tenant, ``enabled`` always ``FALSE`` until Sprint-025 wires a real
  provider.

No changes needed to ``organizations``/``business_units``/``branches``/
``users``/``roles``/``role_assignments`` — their 0002/0003 shape already
matches Sprint-021's org-hierarchy and user-management spec exactly.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from scripts.db.migrations.alembic._ddl_helpers import add_enum_check, drop_check

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_OLD_STATUS_VALUES = ("PROVISIONING", "ACTIVE", "SUSPENDED", "DEPROVISIONING", "DELETED")
_NEW_STATUS_VALUES = ("TRIAL", "SANDBOX", "PRODUCTION", "SUSPENDED", "CANCELLED", "DELETING", "DELETED")

_UPGRADE_STATUS_MAP = {
    "PROVISIONING": "TRIAL",
    "ACTIVE": "PRODUCTION",
    "DEPROVISIONING": "DELETING",
}
_DOWNGRADE_STATUS_MAP = {
    "TRIAL": "PROVISIONING",
    "SANDBOX": "PROVISIONING",
    "PRODUCTION": "ACTIVE",
    "CANCELLED": "SUSPENDED",
    "DELETING": "DEPROVISIONING",
}


def upgrade() -> None:
    drop_check("tenants", "ck_tenants_status_enum")
    for old_value, new_value in _UPGRADE_STATUS_MAP.items():
        op.execute(f"UPDATE tenants SET status = '{new_value}' WHERE status = '{old_value}';")
    op.execute("ALTER TABLE tenants ALTER COLUMN status SET DEFAULT 'TRIAL';")
    add_enum_check("tenants", "ck_tenants_status_enum", "status", _NEW_STATUS_VALUES)

    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ;")
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ;")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS invitations (
            invitation_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            email                   TEXT        NOT NULL,
            role_id                 UUID        NOT NULL REFERENCES roles (role_id) ON DELETE CASCADE,
            org_scope_type          TEXT        NOT NULL,
            org_scope_id            TEXT        NOT NULL,
            token_hash              TEXT        NOT NULL,
            status                  TEXT        NOT NULL DEFAULT 'PENDING',
            invited_by              UUID        NOT NULL,
            expires_at              TIMESTAMPTZ NOT NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            accepted_at             TIMESTAMPTZ,

            CONSTRAINT uq_invitation_token_hash UNIQUE (token_hash)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_invitations_tenant_id ON invitations (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_invitations_email ON invitations (tenant_id, email);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_invitations_status ON invitations (status);")

    add_enum_check(
        "invitations",
        "ck_invitations_status_enum",
        "status",
        ("PENDING", "ACCEPTED", "EXPIRED", "REVOKED"),
    )
    add_enum_check(
        "invitations",
        "ck_invitations_org_scope_type_enum",
        "org_scope_type",
        ("TENANT", "ORG", "BUSINESS_UNIT", "BRANCH"),
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sso_config (
            tenant_id               UUID        PRIMARY KEY REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            provider                TEXT        NOT NULL DEFAULT 'NONE',
            enabled                 BOOLEAN     NOT NULL DEFAULT FALSE,
            config                  JSONB       NOT NULL DEFAULT '{}'::jsonb,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    add_enum_check(
        "sso_config",
        "ck_sso_config_provider_enum",
        "provider",
        ("NONE", "SAML", "OIDC"),
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sso_config CASCADE;")

    drop_check("invitations", "ck_invitations_org_scope_type_enum")
    drop_check("invitations", "ck_invitations_status_enum")
    op.execute("DROP TABLE IF EXISTS invitations CASCADE;")

    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS suspended_at;")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS activated_at;")

    drop_check("tenants", "ck_tenants_status_enum")
    for old_value, new_value in _DOWNGRADE_STATUS_MAP.items():
        op.execute(f"UPDATE tenants SET status = '{new_value}' WHERE status = '{old_value}';")
    op.execute("ALTER TABLE tenants ALTER COLUMN status SET DEFAULT 'PROVISIONING';")
    add_enum_check("tenants", "ck_tenants_status_enum", "status", _OLD_STATUS_VALUES)
