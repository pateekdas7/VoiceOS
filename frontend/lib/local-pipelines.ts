// Pipeline API client — backed by real BFF endpoints (ADR-005 §14).
// Previously backed by localStorage during the sprint where backend wasn't
// ready ("no need to connect the pipeline backend, I will tell you further").
// That sprint is complete: pipelines now live in Postgres.

export type LocalPipelineStatus = "DRAFT" | "ACTIVE" | "PAUSED" | "ARCHIVED";

export type LocalPipeline = {
  pipeline_id: string;
  campaign_id: string;
  name: string;
  status: LocalPipelineStatus;
  created_at: string;
  updated_at?: string;
  created_by?: string;
};

const BFF = process.env.NEXT_PUBLIC_BFF_URL ?? "/bff";

export async function listPipelines(campaignId: string): Promise<LocalPipeline[]> {
  const res = await fetch(`${BFF}/campaigns/${campaignId}/pipelines`, {
    credentials: "include",
  });
  if (!res.ok) return [];
  const data: unknown = await res.json();
  return Array.isArray(data) ? (data as LocalPipeline[]) : [];
}

export async function getPipeline(
  campaignId: string,
  pipelineId: string,
): Promise<LocalPipeline | null> {
  const res = await fetch(`${BFF}/campaigns/${campaignId}/pipelines/${pipelineId}`, {
    credentials: "include",
  });
  if (!res.ok) return null;
  return (await res.json()) as LocalPipeline;
}

export async function createPipeline(
  campaignId: string,
  name: string,
): Promise<LocalPipeline> {
  const res = await fetch(`${BFF}/campaigns/${campaignId}/pipelines`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { error?: { message?: string } }).error?.message ?? "Failed to create pipeline");
  }
  return (await res.json()) as LocalPipeline;
}

export async function updatePipeline(
  campaignId: string,
  pipelineId: string,
  patch: { name?: string; status?: LocalPipelineStatus },
): Promise<LocalPipeline> {
  const res = await fetch(`${BFF}/campaigns/${campaignId}/pipelines/${pipelineId}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { error?: { message?: string } }).error?.message ?? "Failed to update pipeline");
  }
  return (await res.json()) as LocalPipeline;
}
