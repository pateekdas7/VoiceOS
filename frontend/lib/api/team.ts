// Typed client for the Client -> Team Members BFF routes (ADR-005 §6.8).

export type TeamMember = {
  user_id: string;
  email: string;
  name: string;
  is_active: boolean;
  roles: { role_id: string; scope_type: string; scope_id: string }[];
  created_at: string;
};

export type TeamRole = { role_id: string; name: string; permissions: string[] };

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

export async function listTeam(): Promise<TeamMember[]> {
  const res = await fetch(`${bffUrl()}/team`, { credentials: "include" });
  return handle<TeamMember[]>(res);
}

export async function listRoles(): Promise<TeamRole[]> {
  const res = await fetch(`${bffUrl()}/team/roles`, { credentials: "include" });
  return handle<TeamRole[]>(res);
}

export async function inviteTeamMember(input: { email: string; role_id: string }): Promise<unknown> {
  const res = await fetch(`${bffUrl()}/team/invite`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return handle(res);
}

export async function deactivateTeamMember(userId: string): Promise<TeamMember> {
  const res = await fetch(`${bffUrl()}/team/${encodeURIComponent(userId)}`, {
    method: "DELETE",
    credentials: "include",
  });
  return handle<TeamMember>(res);
}
