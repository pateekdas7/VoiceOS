"use client";

import { useEffect, useState } from "react";
import { StatTile } from "@/components/ui/card";
import { listClients } from "@/lib/api/clients";
import { getGpuFleetHealth, listAlerts, listPlatformUsers } from "@/lib/api/admin-ops";

// Platform-wide at-a-glance overview -- each tile reads a real, already-wired
// endpoint (Clients, Platform Users, Alerts Center, GPU Fleet); no synthetic
// MRR/ARR aggregation exists yet (saas_ops has no such rollup query today).
export function AdminDashboardView() {
  const [counts, setCounts] = useState<{
    clients: number | null;
    platformUsers: number | null;
    openAlerts: number | null;
    fleetHealthPct: number | null;
  }>({ clients: null, platformUsers: null, openAlerts: null, fleetHealthPct: null });

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([listClients(), listPlatformUsers(), listAlerts(), getGpuFleetHealth()]).then(
      ([clients, users, alerts, fleet]) => {
        if (cancelled) return;
        setCounts({
          clients: clients.status === "fulfilled" ? clients.value.length : null,
          platformUsers: users.status === "fulfilled" ? users.value.length : null,
          openAlerts: alerts.status === "fulfilled" ? alerts.value.length : null,
          fleetHealthPct: fleet.status === "fulfilled" ? fleet.value.fleet_health_score * 100 : null,
        });
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">Platform Overview</h1>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatTile label="Clients" value={counts.clients === null ? "…" : String(counts.clients)} />
        <StatTile label="Platform Staff" value={counts.platformUsers === null ? "…" : String(counts.platformUsers)} />
        <StatTile label="Open Alerts" value={counts.openAlerts === null ? "…" : String(counts.openAlerts)} />
        <StatTile
          label="GPU Fleet Health"
          value={counts.fleetHealthPct === null ? "…" : `${counts.fleetHealthPct.toFixed(0)}%`}
        />
      </div>
      <p className="text-xs text-muted">
        No MRR/ARR rollup is shown here -- saas_ops has no such aggregation query yet; Revenue&apos;s per-tenant executive
        summary is the closest real financial view currently wired.
      </p>
    </div>
  );
}
