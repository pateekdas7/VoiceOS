// Typed client for the Client -> Campaigns BFF routes (ADR-005 §6.2).
// Every call sends credentials so the voiceos_session cookie reaches the BFF.

import { ApiError, bffGet, bffPost } from "@/lib/api/fetch-client";
export { ApiError };

export type Campaign = {
  campaign_id: string;
  tenant_id: string;
  name: string;
  description: string;
  status: "DRAFT" | "REVIEW" | "APPROVED" | "ACTIVE" | "PAUSED" | "COMPLETED" | "ARCHIVED";
  target_call_count: number;
  completed_call_count: number;
  daily_start_hour: number;
  daily_end_hour: number;
  timezone: string;
  created_at: string;
  updated_at: string;
  created_by: string;
};

export type ImportStatus = {
  import_id: string;
  campaign_id: string;
  filename: string;
  status: "PROCESSING" | "DONE" | "FAILED";
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  duplicate_rows: number;
  last_processed_row: number;
  created_at: string;
  completed_at: string | null;
};

export const listCampaigns = () => bffGet<Campaign[]>("/campaigns");

export const getCampaign = (campaignId: string) =>
  bffGet<Campaign>(`/campaigns/${encodeURIComponent(campaignId)}`);

export const createCampaign = (input: { name: string; description?: string }) =>
  bffPost<Campaign>("/campaigns", input);

export const LIFECYCLE_ACTIONS = [
  "submit-for-review",
  "approve",
  "start",
  "pause",
  "resume",
  "complete",
  "archive",
] as const;
export type LifecycleAction = (typeof LIFECYCLE_ACTIONS)[number];

export const runLifecycleAction = (
  campaignId: string,
  action: LifecycleAction,
  body?: { target_call_count?: number },
) => bffPost<Campaign>(`/campaigns/${encodeURIComponent(campaignId)}/${action}`, body);

export const getImportStatus = (campaignId: string, importId: string) =>
  bffGet<ImportStatus>(`/campaigns/${encodeURIComponent(campaignId)}/leads/imports/${encodeURIComponent(importId)}`);
