"use client";

import { useEffect, useState } from "react";
import { listClients, type Tenant } from "@/lib/api/clients";

// Shared "pick one client" selector -- every per-tenant admin drill-down page
// (Audit Logs, Security, Compliance, Billing, Revenue) reuses the same real
// Admin -> Clients list rather than each re-fetching its own copy.
export function TenantSelect({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (tenantId: string) => void;
}) {
  const [clients, setClients] = useState<Tenant[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    listClients().then((data) => {
      if (cancelled) return;
      setClients(data);
      if (data.length > 0 && !value) onChange(data[0].tenant_id);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (clients === null) {
    return <p className="text-sm text-muted">Loading clients…</p>;
  }
  if (clients.length === 0) {
    return <p className="text-sm text-muted">No clients yet -- create one under Admin → Clients first.</p>;
  }

  return (
    <select
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
    >
      {clients.map((c) => (
        <option key={c.tenant_id} value={c.tenant_id}>
          {c.display_name}
        </option>
      ))}
    </select>
  );
}
