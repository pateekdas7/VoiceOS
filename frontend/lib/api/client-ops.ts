// Typed client for the Client -> CRM / Collections / Leads / Reports / Analytics
// BFF routes (ADR-005 §6.4/6.5/6.7/6.9).
//
// CRM routes use the Python web_api (/webapi) — the BFF has no /crm/* routes.
// Analytics dashboard uses the Python web_api — the BFF has no /analytics/dashboard route.
// All other client-ops routes use the BFF (/bff).

import { ApiError, bffGet, bffPost, webapiGet, webapiPost } from "@/lib/api/fetch-client";
export { ApiError };

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

// CRM customers live in the Python web_api — route to /webapi/crm/customers.
export const listCustomers = () => webapiGet<Customer[]>("/crm/customers");
export const createCustomer = (input: {
  crm_id: string;
  name: string;
  preferred_language?: string;
  contacts?: { contact_type: string; value: string; is_primary?: boolean }[];
}) => webapiPost<Customer>("/crm/customers", input);

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

export const listEscalations = () => bffGet<Escalation[]>("/collections/escalations");
export const resolveEscalation = (id: string, resolutionNotes: string) =>
  bffPost(`/collections/escalations/${encodeURIComponent(id)}/resolve`, { resolution_notes: resolutionNotes });

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

export const listLeads = (campaignId: string) =>
  bffGet<Lead[]>(`/leads?campaign_id=${encodeURIComponent(campaignId)}`);

// -- Reports ------------------------------------------------------------------

export type ReportRun = { tenant_id: string; campaign_id: string | null; day: string; ran_at: string };

export const listReportRuns = () => bffGet<ReportRun[]>("/reports/runs");
export const triggerReportRun = (day: string, campaignId?: string) =>
  bffPost("/reports/runs", { day, campaign_id: campaignId });

// -- Analytics ----------------------------------------------------------------

export type DashboardSnapshot = {
  tenant_id: string;
  as_of: string;
  calls_completed: number;
  outcome_distribution: Record<string, number>;
  average_duration_ms: number;
  contactability_rate: number;
  recovery_rate: number;
  // Populated after Phase 10 backend fix (amount_collected_minor in settlement join)
  amount_collected_minor?: number;
};

// Dashboard snapshot lives in web_api — route to /webapi/analytics/dashboard.
export const getDashboardSnapshot = (campaignId?: string) =>
  webapiGet<DashboardSnapshot>(
    `/analytics/dashboard${campaignId ? `?campaign_id=${encodeURIComponent(campaignId)}` : ""}`,
  );

// Campaign-level summary lives in bff.js at /analytics/campaigns/:id/summary.
export const getCampaignAnalyticsSummary = (campaignId: string) =>
  bffGet<{ ptp_rate: number; contactability_rate: number; conversion_rate: number }>(
    `/analytics/campaigns/${encodeURIComponent(campaignId)}/summary`,
  );
