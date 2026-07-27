"use client";

import { useEffect, useState } from "react";
import { TenantSelect } from "@/components/admin/tenant-select";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { ApiError, listAuditLogs, type AuditEvent } from "@/lib/api/admin-ops";

export function AuditLogsView() {
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    let cancelled = false;
    listAuditLogs(tenantId)
      .then((data) => {
        if (cancelled) return;
        setEvents(data);
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
        <h1 className="text-lg font-semibold">Audit Logs</h1>
        <TenantSelect value={tenantId} onChange={setTenantId} />
      </div>

      {error ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
        </Card>
      ) : events === null ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted">
            {tenantId ? "Loading audit trail…" : "Select a client."}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>{events.length} admin-portal audit event{events.length === 1 ? "" : "s"}</CardTitle>
          </CardHeader>
          {events.length === 0 ? (
            <CardContent className="py-10 text-center text-sm text-muted">
              No admin-portal actions recorded yet for this tenant.
            </CardContent>
          ) : (
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>Action</TableHeaderCell>
                  <TableHeaderCell>Resource</TableHeaderCell>
                  <TableHeaderCell>Actor</TableHeaderCell>
                  <TableHeaderCell>Outcome</TableHeaderCell>
                  <TableHeaderCell>Recorded</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {events.map((e) => (
                  <TableRow key={e.audit_id}>
                    <TableCell className="font-medium">{e.action}</TableCell>
                    <TableCell className="text-muted">
                      {e.resource_type}/{e.resource_id}
                    </TableCell>
                    <TableCell className="text-muted">{e.actor_id}</TableCell>
                    <TableCell className="text-muted">{e.outcome}</TableCell>
                    <TableCell className="text-muted">{new Date(e.recorded_at).toLocaleString()}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Card>
      )}
    </div>
  );
}
