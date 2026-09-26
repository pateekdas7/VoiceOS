// Typed client for the Admin -> Clients BFF routes (ADR-005 §6.1).
// Every call sends credentials so the voiceos_session cookie reaches the BFF.

import { ApiError, bffGet, bffPost } from "@/lib/api/fetch-client";
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

export const listClients = () => bffGet<Tenant[]>("/admin/clients");

export const getClient = (tenantId: string) =>
  bffGet<Tenant>(`/admin/clients/${encodeURIComponent(tenantId)}`);

export const createClient = (input: {
  slug: string;
  display_name: string;
  subscription_tier: string;
}) => bffPost<Tenant>("/admin/clients", input);

export const suspendClient = (tenantId: string) =>
  bffPost<Tenant>(`/admin/clients/${encodeURIComponent(tenantId)}/suspend`);

export const reactivateClient = (tenantId: string) =>
  bffPost<Tenant>(`/admin/clients/${encodeURIComponent(tenantId)}/reactivate`);
