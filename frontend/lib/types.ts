// Actor model per ADR-005 §3. Two actor kinds, never interchangeable.

export type PlatformRole = "PLATFORM_ADMIN" | "PLATFORM_SUPPORT" | "PLATFORM_BILLING_OPS";

// The 5 existing tenant roles, unchanged from src/services/authz/roles.py.
export type TenantRole = "ADMIN" | "SUPERVISOR" | "MANAGER" | "AGENT" | "AUDITOR";

export type PlatformActor = {
  kind: "PLATFORM_OWNER";
  userId: string;
  platformRole: PlatformRole;
  email: string;
};

export type TenantActor = {
  kind: "TENANT_USER";
  userId: string;
  tenantId: string;
  role: TenantRole;
  email: string;
};

export type Actor = PlatformActor | TenantActor;

export type HealthStatus = "healthy" | "warning" | "critical" | "unknown";

export type HealthComponent = {
  component: string;
  /** Raw backend value: "healthy" | "degraded" | "unhealthy" (src.libs.health.protocol.HealthStatus).
   *  Not the same vocabulary as this file's HealthStatus -- always pass through lib/health.ts's
   *  toHealthStatus() before comparing against "warning"/"critical"/"unknown". */
  status: string;
  latencyMs: number | null;
  lastChecked: string | null;
};
