// Typed client for the Admin -> Audit Logs / Security / Compliance / Billing /
// Infrastructure / Users & Roles / Platform Settings / Analytics / Monitoring
// BFF routes (ADR-005 §6.8/6.9/6.16-18/12.1-12.4, ADR-006 monitoring).

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function bffUrl(): string {
  const url = process.env.NEXT_PUBLIC_BFF_URL;
  if (!url) throw new ApiError(0, "NO_BFF_URL", "NEXT_PUBLIC_BFF_URL is not configured");
  return url;
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(res.status, body?.error?.code ?? "UNKNOWN", body?.error?.message ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

async function get<T>(path: string): Promise<T> {
  return handle<T>(await fetch(`${bffUrl()}${path}`, { credentials: "include" }));
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  return handle<T>(
    await fetch(`${bffUrl()}${path}`, {
      method: "POST",
      credentials: "include",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    }),
  );
}

// -- Users & Roles --------------------------------------------------------

export type PlatformUser = {
  platform_user_id: string;
  email: string;
  name: string;
  platform_role: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export const listPlatformUsers = () => get<PlatformUser[]>("/admin/platform-users");
export const createPlatformUser = (input: { email: string; name: string; platform_role: string }) =>
  post<PlatformUser>("/admin/platform-users", input);
export const deactivatePlatformUser = (id: string) =>
  post<PlatformUser>(`/admin/platform-users/${encodeURIComponent(id)}/deactivate`);

// -- Audit Logs -------------------------------------------------------------

export type AuditEvent = {
  audit_id: string;
  actor_id: string;
  action: string;
  resource_type: string;
  resource_id: string;
  outcome: string;
  recorded_at: string;
};

export const listAuditLogs = (tenantId: string) =>
  get<AuditEvent[]>(`/admin/clients/${encodeURIComponent(tenantId)}/audit-logs`);

// -- Security -----------------------------------------------------------

export type SecuritySummary = {
  counts_by_action: Record<string, number>;
  recent_events: {
    audit_id: string;
    actor_id: string;
    action: string;
    resource_type: string;
    resource_id: string;
    outcome: string;
    recorded_at: string | null;
  }[];
};

export const getSecuritySummary = (tenantId: string) =>
  get<SecuritySummary>(`/admin/clients/${encodeURIComponent(tenantId)}/security-summary`);

// -- Compliance -----------------------------------------------------------

export type ComplianceStatus = {
  tenant_id: string;
  status: "COMPLIANT" | "VIOLATION";
  rules: { rule_id: string; matches_action: string; threshold_count: number; window_seconds: number; alert_kind: string }[];
};

export const getComplianceStatus = (tenantId: string) =>
  get<ComplianceStatus>(`/admin/clients/${encodeURIComponent(tenantId)}/compliance-status`);

// -- Billing + Revenue ------------------------------------------------------

export type Subscription = {
  subscription_id: string;
  tenant_id: string;
  tier: string;
  contract_start: string;
  contract_end: string | null;
  base_fee_minor: number;
  currency: string;
  is_active: boolean;
};

export async function getSubscription(tenantId: string): Promise<Subscription | null> {
  try {
    return await get<Subscription>(`/admin/clients/${encodeURIComponent(tenantId)}/subscription`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

export const createSubscription = (tenantId: string, tier: string) =>
  post<Subscription>(`/admin/clients/${encodeURIComponent(tenantId)}/subscription`, { tier });

export const generateInvoice = (tenantId: string, periodStart: string, periodEnd: string) =>
  post(`/admin/clients/${encodeURIComponent(tenantId)}/invoices`, {
    period_start: periodStart,
    period_end: periodEnd,
  });

// -- Platform Settings (feature flags) -------------------------------------

export type FeatureFlagCatalogEntry = {
  flag_name: string;
  targeting_rows: { scope: string; scope_value: string | null; state: string; rollout_percentage: number }[];
};

export const listFeatureFlags = () => get<FeatureFlagCatalogEntry[]>("/admin/feature-flags");
export const setFeatureFlag = (
  flagName: string,
  input: { scope: string; state: string; scope_value?: string; rollout_percentage?: number },
) => post(`/admin/feature-flags/${encodeURIComponent(flagName)}`, input);

// -- Infrastructure ---------------------------------------------------------

export type GPUFleetHealth = {
  fleet_health_score: number;
  is_degraded: boolean;
  is_severely_degraded: boolean;
  nodes: { node_id: string; healthy: boolean; vram_used_mb: number; vram_total_mb: number }[];
};

export const getGpuFleetHealth = () => get<GPUFleetHealth>("/admin/infrastructure/gpu-fleet");

// -- Analytics (executive summary) ------------------------------------------

export type ExecutiveSummary = {
  tenant_id: string;
  gross_recovery_rate: number;
  cost_per_conversation_minor: number;
  mom_improvement: number;
  slo_attainment: number;
  compliance_score: number;
};

export const getExecutiveSummary = (tenantId: string) =>
  get<ExecutiveSummary>(`/admin/clients/${encodeURIComponent(tenantId)}/executive-summary`);

// -- Monitoring: Alerts Center ----------------------------------------------

export type AlertRecord = {
  alert_id: string;
  tenant_id: string | null;
  source: string;
  fingerprint: string;
  severity: "critical" | "warning" | "info";
  status: "firing" | "acknowledged" | "escalated" | "resolved";
  fired_at: string;
  labels: Record<string, string>;
  annotations: Record<string, string>;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  escalated_at: string | null;
  escalated_to: string | null;
  resolved_at: string | null;
};

export const listAlerts = () => get<AlertRecord[]>("/admin/alerts");
export const acknowledgeAlert = (id: string) => post<AlertRecord>(`/admin/alerts/${encodeURIComponent(id)}/acknowledge`);
export const resolveAlert = (id: string) => post<AlertRecord>(`/admin/alerts/${encodeURIComponent(id)}/resolve`);

// -- Monitoring: AI Insights -------------------------------------------------

export type Insight = {
  insight_id: string;
  tenant_id: string | null;
  category: string;
  severity: string;
  verified_facts: { claim: string; source: string; query: string; value: string; observed_at: string }[];
  hypotheses: { claim: string; reasoning: string; confidence: string }[];
  affected_components: string[];
  confidence_level: string;
  model: string;
  recommendation: string | null;
  generated_at: string;
};

export const listInsights = (tenantId?: string) =>
  get<{ reasoning_enabled: boolean; insights: Insight[] }>(
    `/admin/ai-insights${tenantId ? `?tenant_id=${encodeURIComponent(tenantId)}` : ""}`,
  );

// -- Monitoring: AI Reports ---------------------------------------------------

export type AIReport = {
  report_id: string;
  report_type: string;
  period_start: string;
  period_end: string;
  scope_level: string;
  tenant_id: string | null;
  severity: string;
  affected_components: string[];
  business_impact: string;
  recommended_actions: string[];
  confidence_level: string;
  narrative: string;
  generated_at: string;
  delivered_to: string[];
};

export const listAIReports = (tenantId?: string) =>
  get<{ reasoning_enabled: boolean; reports: AIReport[] }>(
    `/admin/ai-reports${tenantId ? `?tenant_id=${encodeURIComponent(tenantId)}` : ""}`,
  );

// -- Monitoring: Capacity Planning -------------------------------------------

export type CapacityForecast = {
  forecast_id: string;
  tenant_id: string | null;
  resource: string;
  horizon_days: number;
  forecast_data: Record<string, unknown>;
  headroom_pct: number;
  confidence: string;
  generated_at: string;
};

export const listCapacityForecasts = (resource: string) =>
  get<{ reasoning_enabled: boolean; forecasts: CapacityForecast[] }>(
    `/admin/capacity-forecasts?resource=${encodeURIComponent(resource)}`,
  );

// -- Monitoring: Incident Timeline -------------------------------------------

export type TimelineEntry = {
  type: "alert" | "insight";
  timestamp: string;
  event: AlertRecord | Insight;
};

export const getIncidentTimeline = () => get<TimelineEntry[]>("/admin/incident-timeline");
