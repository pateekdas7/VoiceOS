// Typed client for the Client -> Campaigns BFF routes (ADR-005 §6.2).
// Every call sends credentials so the voiceos_session cookie reaches the BFF.

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

export async function listCampaigns(): Promise<Campaign[]> {
  const res = await fetch(`${bffUrl()}/campaigns`, { credentials: "include" });
  return handle<Campaign[]>(res);
}

export async function getCampaign(campaignId: string): Promise<Campaign> {
  const res = await fetch(`${bffUrl()}/campaigns/${encodeURIComponent(campaignId)}`, { credentials: "include" });
  return handle<Campaign>(res);
}

export async function createCampaign(input: { name: string; description?: string }): Promise<Campaign> {
  const res = await fetch(`${bffUrl()}/campaigns`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return handle<Campaign>(res);
}

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

export async function runLifecycleAction(
  campaignId: string,
  action: LifecycleAction,
  body?: { target_call_count?: number },
): Promise<Campaign> {
  const res = await fetch(`${bffUrl()}/campaigns/${encodeURIComponent(campaignId)}/${action}`, {
    method: "POST",
    credentials: "include",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  return handle<Campaign>(res);
}
