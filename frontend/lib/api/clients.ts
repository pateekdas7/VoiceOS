// Typed client for the Admin -> Clients routes (ADR-005 §6.1).
// Routes through webapi (/webapi rewrite) — the Python web_api has the full
// TenantService implementation including create/suspend/reactivate. The BFF
// only proxies GET list/get; all mutations go directly to webapi.

import { ApiError, webapiGet, webapiPost } from "@/lib/api/fetch-client";
export { ApiError };

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

export const listClients = () => webapiGet<Tenant[]>("/admin/clients");

export const getClient = (tenantId: string) =>
  webapiGet<Tenant>(`/admin/clients/${encodeURIComponent(tenantId)}`);

export const createClient = (input: {
  slug: string;
  display_name: string;
  subscription_tier: string;
}) => webapiPost<Tenant>("/admin/clients", input);

export const suspendClient = (tenantId: string) =>
  webapiPost<Tenant>(`/admin/clients/${encodeURIComponent(tenantId)}/suspend`);

export const reactivateClient = (tenantId: string) =>
  webapiPost<Tenant>(`/admin/clients/${encodeURIComponent(tenantId)}/reactivate`);
