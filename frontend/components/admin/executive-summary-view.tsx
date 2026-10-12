"use client";

import { useEffect, useState } from "react";
import { TenantSelect } from "@/components/admin/tenant-select";
import { Card, CardContent, StatTile } from "@/components/ui/card";
import { ApiError, getExecutiveSummary, type ExecutiveSummary } from "@/lib/api/admin-ops";

// Shared by both /admin/revenue and /admin/analytics -- ExecutiveDashboard's
// KPI set (recovery rate, cost-per-conversation, SLO, compliance) is the one
// real bi_platform-backed summary this pass wires; both pages read the same
// data through a different framing rather than duplicating a second query.
export function ExecutiveSummaryView({ title }: { title: string }) {
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [summary, setSummary] = useState<ExecutiveSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    let cancelled = false;
    getExecutiveSummary(tenantId)
      .then((data) => {
        if (cancelled) return;
        setSummary(data);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
      });
    return () => {
      cancelled = true;
    };
  }, [tenantId]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">{title}</h1>
        <TenantSelect value={tenantId} onChange={setTenantId} />
      </div>

      {error ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
        </Card>
      ) : summary === null ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted">
            {tenantId ? "Loading summary…" : "Select a client."}
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatTile label="Gross Recovery Rate" value={`${(summary.gross_recovery_rate * 100).toFixed(1)}%`} />
          <StatTile label="MoM Improvement" value={`${(summary.mom_improvement * 100).toFixed(1)}%`} />
          <StatTile label="Cost / Conversation" value={`₹${(summary.cost_per_conversation_minor / 100).toFixed(2)}`} />
          <StatTile label="Compliance Score" value={`${(summary.compliance_score * 100).toFixed(1)}%`} />
        </div>
      )}
    </div>
  );
}
