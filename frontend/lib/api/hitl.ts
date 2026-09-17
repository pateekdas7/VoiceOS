// Typed client for the Client -> Live Calls -> Escalations (HITL) BFF routes (ADR-005 §12.2).
// Every call sends credentials so the voiceos_session cookie reaches the BFF.

export type HITLPriority = "CRITICAL" | "HIGH" | "MEDIUM";
export type HITLItemStatus = "PENDING" | "CLAIMED" | "RESOLVED";

export type HITLItem = {
  hitl_item_id: string;
  call_id: string;
  reason: string;
  priority: HITLPriority;
  status: HITLItemStatus;
  context: Record<string, unknown>;
  enqueued_at: string;
  claimed_by: string | null;
  claimed_at: string | null;
  resolved_at: string | null;
  sla_deadline_at: string;
  sla_breached: boolean;
};

export type HITLDecisionRecord = {
  hitl_decision_id: string;
  hitl_item_id: string;
  decision: string;
  supervisor_id: string;
  rationale: string;
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

export async function listHITLQueue(): Promise<HITLItem[]> {
  const res = await fetch(`${bffUrl()}/hitl/queue`, { credentials: "include" });
  return handle<HITLItem[]>(res);
}

export async function claimNextHITLItem(): Promise<HITLItem | null> {
  const res = await fetch(`${bffUrl()}/hitl/queue/claim-next`, { method: "POST", credentials: "include" });
  if (res.status === 404) return null;
  return handle<HITLItem>(res);
}

export async function recordHITLDecision(
  hitlItemId: string,
  decision: string,
  rationale: string,
): Promise<HITLDecisionRecord> {
  const res = await fetch(`${bffUrl()}/hitl/items/${encodeURIComponent(hitlItemId)}/decision`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, rationale }),
  });
  return handle<HITLDecisionRecord>(res);
}
