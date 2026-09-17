// Typed client for the Admin -> Clients BFF routes (ADR-005 §6.1).
// Every call sends credentials so the voiceos_session cookie reaches the BFF.

export type Tenant = {
  tenant_id: string;
  slug: string;
  display_name: string;
  subscription_tier: string;
  isolation_profile: string;
  status: string;
  timezone: string;
  currency: string;
  max_concurrent_calls: number;
  feature_flags: string[];
  created_at: string;
  updated_at: string;
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

export async function listClients(): Promise<Tenant[]> {
  const res = await fetch(`${bffUrl()}/admin/clients`, { credentials: "include" });
  return handle<Tenant[]>(res);
}

export async function getClient(tenantId: string): Promise<Tenant> {
  const res = await fetch(`${bffUrl()}/admin/clients/${encodeURIComponent(tenantId)}`, { credentials: "include" });
  return handle<Tenant>(res);
}

export async function createClient(input: {
  slug: string;
  display_name: string;
  subscription_tier: string;
}): Promise<Tenant> {
  const res = await fetch(`${bffUrl()}/admin/clients`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return handle<Tenant>(res);
}

export async function suspendClient(tenantId: string): Promise<Tenant> {
  const res = await fetch(`${bffUrl()}/admin/clients/${encodeURIComponent(tenantId)}/suspend`, {
    method: "POST",
    credentials: "include",
  });
  return handle<Tenant>(res);
}

export async function reactivateClient(tenantId: string): Promise<Tenant> {
  const res = await fetch(`${bffUrl()}/admin/clients/${encodeURIComponent(tenantId)}/reactivate`, {
    method: "POST",
    credentials: "include",
  });
  return handle<Tenant>(res);
}
