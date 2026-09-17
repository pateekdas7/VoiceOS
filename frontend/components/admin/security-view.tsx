"use client";

import { useEffect, useState } from "react";
import { TenantSelect } from "@/components/admin/tenant-select";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { ApiError, getSecuritySummary, type SecuritySummary } from "@/lib/api/admin-ops";

export function SecurityView() {
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [summary, setSummary] = useState<SecuritySummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    let cancelled = false;
    getSecuritySummary(tenantId)
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
        <h1 className="text-lg font-semibold">Security</h1>
        <TenantSelect value={tenantId} onChange={setTenantId} />
      </div>

      {error ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
        </Card>
      ) : summary === null ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted">
            {tenantId ? "Loading security summary…" : "Select a client."}
          </CardContent>
        </Card>
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Security-relevant events by action</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-3">
              {Object.entries(summary.counts_by_action).length === 0 ? (
                <p className="text-sm text-muted">No security-relevant events recorded yet for this tenant.</p>
              ) : (
                Object.entries(summary.counts_by_action).map(([action, count]) => (
                  <Badge key={action} tone={action.includes("failed") || action.includes("block") ? "critical" : "neutral"}>
                    {action}: {count}
                  </Badge>
                ))
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Recent events</CardTitle>
            </CardHeader>
            {summary.recent_events.length === 0 ? (
              <CardContent className="py-10 text-center text-sm text-muted">No recent events.</CardContent>
            ) : (
              <Table>
                <TableHead>
                  <TableRow>
                    <TableHeaderCell>Action</TableHeaderCell>
                    <TableHeaderCell>Actor</TableHeaderCell>
                    <TableHeaderCell>Outcome</TableHeaderCell>
                    <TableHeaderCell>Recorded</TableHeaderCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {summary.recent_events.map((e) => (
                    <TableRow key={e.audit_id}>
                      <TableCell className="font-medium">{e.action}</TableCell>
                      <TableCell className="text-muted">{e.actor_id}</TableCell>
                      <TableCell className="text-muted">{e.outcome}</TableCell>
                      <TableCell className="text-muted">
                        {e.recorded_at ? new Date(e.recorded_at).toLocaleString() : "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
