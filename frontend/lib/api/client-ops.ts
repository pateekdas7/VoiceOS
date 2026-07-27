// Typed client for the Client -> CRM / Collections / Leads / Reports / Analytics
// BFF routes (ADR-005 §6.4/6.5/6.7/6.9).

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

// -- CRM ----------------------------------------------------------------

export type CustomerContact = { contact_type: string; value: string; is_primary: boolean; is_dnc: boolean };
export type Customer = {
  customer_id: string;
  tenant_id: string;
  crm_id: string;
  name: string;
  preferred_language: string;
  contacts: CustomerContact[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export const listCustomers = () => get<Customer[]>("/crm/customers");
export const createCustomer = (input: {
  crm_id: string;
  name: string;
  preferred_language?: string;
  contacts?: { contact_type: string; value: string; is_primary?: boolean }[];
}) => post<Customer>("/crm/customers", input);

// -- Collections ----------------------------------------------------------

export type Escalation = {
  escalation_id: string;
  tenant_id: string;
  call_id: string;
  customer_id: string;
  reason: string;
  escalated_to: string;
  escalated_at: string;
  resolved_at: string | null;
  resolution_notes: string | null;
};

export const listEscalations = () => get<Escalation[]>("/collections/escalations");
export const resolveEscalation = (id: string, resolutionNotes: string) =>
  post(`/collections/escalations/${encodeURIComponent(id)}/resolve`, { resolution_notes: resolutionNotes });

// -- Leads ------------------------------------------------------------------

export type Lead = {
  campaign_audience_id: string;
  campaign_id: string;
  customer_id: string;
  customer_name: string | null;
  primary_contact: string | null;
  dnd: boolean;
  included_at: string;
  excluded_reason: string;
};

export const listLeads = (campaignId: string) => get<Lead[]>(`/leads?campaign_id=${encodeURIComponent(campaignId)}`);

// -- Reports ------------------------------------------------------------------

export type ReportRun = { tenant_id: string; campaign_id: string | null; day: string; ran_at: string };

export const listReportRuns = () => get<ReportRun[]>("/reports/runs");
export const triggerReportRun = (day: string, campaignId?: string) =>
  post("/reports/runs", { day, campaign_id: campaignId });

// -- Analytics ----------------------------------------------------------------

export type DashboardSnapshot = {
  tenant_id: string;
  as_of: string;
  calls_completed: number;
  outcome_distribution: Record<string, number>;
  average_duration_ms: number;
  contactability_rate: number;
  recovery_rate: number;
};

export const getDashboardSnapshot = (campaignId?: string) =>
  get<DashboardSnapshot>(`/analytics/dashboard${campaignId ? `?campaign_id=${encodeURIComponent(campaignId)}` : ""}`);

export const getCampaignAnalyticsSummary = (campaignId: string) =>
  get<{ ptp_rate: number; contactability_rate: number; conversion_rate: number }>(
    `/analytics/campaigns/${encodeURIComponent(campaignId)}/summary`,
  );
