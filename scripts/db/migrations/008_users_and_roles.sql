-- Migration 008: Users, Roles, and RBAC Role Assignments
-- Architecture: V4 Ch6 (RBAC); V4 Ch7 (Authentication); V5 Ch9 (Admin Portal).

CREATE TABLE IF NOT EXISTS roles (
    role_id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    name                    TEXT        NOT NULL,
    description             TEXT        NOT NULL DEFAULT '',
    permissions             TEXT[]      NOT NULL DEFAULT '{}',
    is_system_role          BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL,
    updated_at              TIMESTAMPTZ NOT NULL,

    CONSTRAINT uq_role_tenant_name UNIQUE (tenant_id, name)
);

CREATE INDEX IF NOT EXISTS idx_roles_tenant_id         ON roles (tenant_id);

-- Users and service accounts
CREATE TABLE IF NOT EXISTS users (
    user_id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    email                   TEXT        NOT NULL,
    name                    TEXT        NOT NULL,
    is_active               BOOLEAN     NOT NULL DEFAULT TRUE,
    is_service_account      BOOLEAN     NOT NULL DEFAULT FALSE,
    mfa_enabled             BOOLEAN     NOT NULL DEFAULT FALSE,
    last_login_at           TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL,
    updated_at              TIMESTAMPTZ NOT NULL,

    CONSTRAINT uq_user_tenant_email UNIQUE (tenant_id, email)
);

CREATE INDEX IF NOT EXISTS idx_users_tenant_id         ON users (tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_email             ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_is_active         ON users (tenant_id, is_active);

-- RBAC role assignments
CREATE TABLE IF NOT EXISTS role_assignments (
    assignment_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID        NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    role_id                 UUID        NOT NULL REFERENCES roles (role_id) ON DELETE CASCADE,
    -- OrgScope
    scope_type              TEXT        NOT NULL,       -- 'TENANT' | 'ORG' | 'BUSINESS_UNIT' | 'BRANCH'
    scope_id                TEXT        NOT NULL,
    assigned_by             UUID        NOT NULL,
    assigned_at             TIMESTAMPTZ NOT NULL,
    expires_at              TIMESTAMPTZ,

    CONSTRAINT uq_role_assignment UNIQUE (user_id, role_id, scope_type, scope_id)
);

CREATE INDEX IF NOT EXISTS idx_ra_user_id              ON role_assignments (user_id);
CREATE INDEX IF NOT EXISTS idx_ra_role_id              ON role_assignments (role_id);
CREATE INDEX IF NOT EXISTS idx_ra_scope                ON role_assignments (scope_type, scope_id);
CREATE INDEX IF NOT EXISTS idx_ra_expires_at           ON role_assignments (expires_at);
