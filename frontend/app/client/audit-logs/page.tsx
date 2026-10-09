"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { ApiError, bffGet } from "@/lib/api/fetch-client";

type AuditEntry = {
  audit_id: string;
  actor_id: string;
  action: string;
  resource_type: string;
  resource_id: string;
  outcome: string;
  recorded_at: string;
};

export default function ClientAuditLogsPage() {
  const [events, setEvents] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    bffGet<AuditEntry[]>("/audit-logs")
      .then((data) => { if (!cancelled) { setEvents(data); } })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not reach backend."); });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">Audit Logs</h1>
      <p className="text-xs text-muted">Your workspace\u2019s immutable audit trail \u2014 every action recorded by actor and outcome.</p>

      {error ? (
        <Card><CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent></Card>
      ) : events === null ? (
        <Card><CardContent className="py-10 text-center text-sm text-muted">Loading\u2026</CardContent></Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>{events.length} event{events.length === 1 ? "" : "s"}</CardTitle>
          </CardHeader>
          {events.length === 0 ? (
            <CardContent className="py-10 text-center text-sm text-muted">No audit events yet.</CardContent>
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
                    <TableCell className="text-muted">{e.resource_type} / {e.resource_id.slice(0, 8)}</TableCell>
                    <TableCell className="text-muted">{e.actor_id.slice(0, 16)}\u2026</TableCell>
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
