// Typed client for the Client -> Live Calls -> Escalations (HITL) BFF routes (ADR-005 §12.2).
// Every call sends credentials so the voiceos_session cookie reaches the BFF.

import { ApiError, bffGet, bffPost, bffUrl, handleResponse } from "@/lib/api/fetch-client";
export { ApiError };

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

export const listHITLQueue = () => bffGet<HITLItem[]>("/hitl/queue");

export async function claimNextHITLItem(): Promise<HITLItem | null> {
  const res = await fetch(`${bffUrl()}/hitl/queue/claim-next`, { method: "POST", credentials: "include" });
  // Check 401 before the 404 guard so session expiry still redirects to login.
  if (res.status === 401) return handleResponse<HITLItem>(res);
  if (res.status === 404) return null;
  return handleResponse<HITLItem>(res);
}

export const recordHITLDecision = (hitlItemId: string, decision: string, rationale: string) =>
  bffPost<HITLDecisionRecord>(`/hitl/items/${encodeURIComponent(hitlItemId)}/decision`, {
    decision,
    rationale,
  });
