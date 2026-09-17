"use client";

import { useEffect, useState } from "react";
import { TenantSelect } from "@/components/admin/tenant-select";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { ApiError, getComplianceStatus, type ComplianceStatus } from "@/lib/api/admin-ops";

export function ComplianceView() {
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [status, setStatus] = useState<ComplianceStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    let cancelled = false;
    getComplianceStatus(tenantId)
      .then((data) => {
        if (cancelled) return;
        setStatus(data);
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
        <h1 className="text-lg font-semibold">Compliance</h1>
        <TenantSelect value={tenantId} onChange={setTenantId} />
      </div>

      {error ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
        </Card>
      ) : status === null ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted">
            {tenantId ? "Loading compliance status…" : "Select a client."}
          </CardContent>
        </Card>
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Posture</CardTitle>
            </CardHeader>
            <CardContent>
              <Badge tone={status.status === "COMPLIANT" ? "healthy" : "critical"}>{status.status}</Badge>
              <p className="mt-2 text-xs text-muted">
                Reflects only signals actually ingested via ComplianceMonitoring.ingest() in this environment --
                stays COMPLIANT until the real-time audit event stream is wired to it.
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Active monitor rules</CardTitle>
            </CardHeader>
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>Rule</TableHeaderCell>
                  <TableHeaderCell>Matches</TableHeaderCell>
                  <TableHeaderCell>Threshold</TableHeaderCell>
                  <TableHeaderCell>Window</TableHeaderCell>
                  <TableHeaderCell>Alert Kind</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {status.rules.map((r) => (
                  <TableRow key={r.rule_id}>
                    <TableCell className="font-medium">{r.rule_id}</TableCell>
                    <TableCell className="text-muted">{r.matches_action}</TableCell>
                    <TableCell className="text-muted">{r.threshold_count}</TableCell>
                    <TableCell className="text-muted">{r.window_seconds}s</TableCell>
                    <TableCell>
                      <Badge tone="warning">{r.alert_kind}</Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        </>
      )}
    </div>
  );
}
