"use client";

import { useEffect, useState } from "react";
import { StatTile } from "@/components/ui/card";
import { listCampaigns } from "@/lib/api/campaigns";
import { listCustomers, listEscalations } from "@/lib/api/client-ops";
import { listHITLQueue } from "@/lib/api/hitl";

// This tenant's own at-a-glance overview -- every tile is tenant-scoped by
// the session's JWT, never a request parameter, reusing already-wired
// Campaigns/CRM/Collections/HITL endpoints rather than a new aggregation.
export function ClientDashboardView() {
  const [counts, setCounts] = useState<{
    activeCampaigns: number | null;
    customers: number | null;
    openEscalations: number | null;
    pendingHitl: number | null;
  }>({ activeCampaigns: null, customers: null, openEscalations: null, pendingHitl: null });

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([listCampaigns(), listCustomers(), listEscalations(), listHITLQueue()]).then(
      ([campaigns, customers, escalations, hitl]) => {
        if (cancelled) return;
        setCounts({
          activeCampaigns:
            campaigns.status === "fulfilled" ? campaigns.value.filter((c) => c.status === "ACTIVE").length : null,
          customers: customers.status === "fulfilled" ? customers.value.length : null,
          openEscalations:
            escalations.status === "fulfilled" ? escalations.value.filter((e) => !e.resolved_at).length : null,
          pendingHitl: hitl.status === "fulfilled" ? hitl.value.length : null,
        });
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">Dashboard</h1>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatTile
          label="Active Campaigns"
          value={counts.activeCampaigns === null ? "…" : String(counts.activeCampaigns)}
        />
        <StatTile label="Customers" value={counts.customers === null ? "…" : String(counts.customers)} />
        <StatTile
          label="Open Escalations"
          value={counts.openEscalations === null ? "…" : String(counts.openEscalations)}
        />
        <StatTile label="Pending HITL Items" value={counts.pendingHitl === null ? "…" : String(counts.pendingHitl)} />
      </div>
    </div>
  );
}
