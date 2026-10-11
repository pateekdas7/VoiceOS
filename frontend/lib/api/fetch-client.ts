// Shared HTTP client for all API files.
// Centralises: ApiError, URL helpers, response handling, and 401 session-expiry redirect.

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export function bffUrl(): string {
  const url = process.env.NEXT_PUBLIC_BFF_URL;
  if (!url) throw new ApiError(0, "NO_BFF_URL", "NEXT_PUBLIC_BFF_URL is not configured");
  return url;
}

export function webapiUrl(): string {
  return process.env.NEXT_PUBLIC_WEBAPI_URL ?? "/webapi";
}

// On 401: redirect browser to login with returnUrl, then throw so the caller's
// promise rejects (preventing any stale UI from rendering).
export async function handleResponse<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    if (typeof window !== "undefined") {
      const returnUrl = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.href = `/login?returnUrl=${returnUrl}`;
    }
    throw new ApiError(401, "UNAUTHORIZED", "Session expired. Redirecting to login…");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(res.status, body?.error?.code ?? "UNKNOWN", body?.error?.message ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

// ── BFF helpers (Node.js :8000) ───────────────────────────────────────────────

export async function bffGet<T>(path: string): Promise<T> {
  return handleResponse<T>(await fetch(`${bffUrl()}${path}`, { credentials: "include" }));
}

export async function bffPost<T>(path: string, body?: unknown): Promise<T> {
  return handleResponse<T>(
    await fetch(`${bffUrl()}${path}`, {
      method: "POST",
      credentials: "include",
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
  );
}

export async function bffPut<T>(path: string, body?: unknown): Promise<T> {
  return handleResponse<T>(
    await fetch(`${bffUrl()}${path}`, {
      method: "PUT",
      credentials: "include",
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
  );
}

export async function bffDel<T>(path: string): Promise<T> {
  return handleResponse<T>(await fetch(`${bffUrl()}${path}`, { method: "DELETE", credentials: "include" }));
}

// ── Web-API helpers (Python :8001) ────────────────────────────────────────────

export async function webapiGet<T>(path: string): Promise<T> {
  return handleResponse<T>(await fetch(`${webapiUrl()}${path}`, { credentials: "include" }));
}

export async function webapiPost<T>(path: string, body?: unknown): Promise<T> {
  return handleResponse<T>(
    await fetch(`${webapiUrl()}${path}`, {
      method: "POST",
      credentials: "include",
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
  );
}

export async function webapiPut<T>(path: string, body?: unknown): Promise<T> {
  return handleResponse<T>(
    await fetch(`${webapiUrl()}${path}`, {
      method: "PUT",
      credentials: "include",
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
  );
}

export async function webapiDel<T>(path: string): Promise<T> {
  return handleResponse<T>(await fetch(`${webapiUrl()}${path}`, { method: "DELETE", credentials: "include" }));
}
