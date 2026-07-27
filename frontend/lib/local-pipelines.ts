// Client-side-only Pipeline store (localStorage), until Pipeline exists as a
// real backend entity (ADR-005 §4.3/§14 -- explicitly its own dedicated
// future sprint, not built here per the user's own instruction: "no need to
// connect the pipeline backend, I will tell you further"). Same function
// shape a real API client would have (list/create/get, all campaign-scoped)
// so swapping this out for real fetch() calls later needs no UI rework --
// every call site already awaits these as if they were async.

export type LocalPipelineStatus = "DRAFT" | "ACTIVE" | "PAUSED" | "ARCHIVED";

export type LocalPipeline = {
  pipeline_id: string;
  campaign_id: string;
  name: string;
  status: LocalPipelineStatus;
  created_at: string;
};

const STORAGE_KEY = "voiceos.pipelines.v1";

function readAll(): LocalPipeline[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as LocalPipeline[]) : [];
  } catch {
    return [];
  }
}

function writeAll(pipelines: LocalPipeline[]): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(pipelines));
}

export async function listPipelines(campaignId: string): Promise<LocalPipeline[]> {
  return readAll()
    .filter((p) => p.campaign_id === campaignId)
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
}

export async function getPipeline(campaignId: string, pipelineId: string): Promise<LocalPipeline | null> {
  return readAll().find((p) => p.campaign_id === campaignId && p.pipeline_id === pipelineId) ?? null;
}

export async function createPipeline(campaignId: string, name: string): Promise<LocalPipeline> {
  const pipeline: LocalPipeline = {
    pipeline_id: crypto.randomUUID(),
    campaign_id: campaignId,
    name,
    status: "DRAFT",
    created_at: new Date().toISOString(),
  };
  const all = readAll();
  all.push(pipeline);
  writeAll(all);
  return pipeline;
}
