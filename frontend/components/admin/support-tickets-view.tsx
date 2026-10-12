"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import {
  ApiError,
  getSlaSummary,
  listSupportTickets,
  updateTicketStatus,
  type SlaSummary,
  type SupportTicket,
} from "@/lib/api/admin-ops";

const PRIORITY_COLORS: Record<string, "critical" | "warning" | "brand" | "neutral"> = {
  CRITICAL: "critical",
  HIGH: "warning",
  MEDIUM: "brand",
  LOW: "neutral",
};

const STATUS_COLORS: Record<string, "brand" | "warning" | "neutral" | "healthy"> = {
  OPEN: "brand",
  IN_PROGRESS: "warning",
  WAITING_ON_CUSTOMER: "neutral",
  RESOLVED: "healthy",
  CLOSED: "neutral",
};

export function SupportTicketsView() {
  const [tickets, setTickets] = useState<SupportTicket[] | null>(null);
  const [sla, setSla] = useState<SlaSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("");

  function load() {
    listSupportTickets(statusFilter ? { status: statusFilter } : undefined)
      .then(setTickets)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load tickets"));
    getSlaSummary()
      .then(setSla)
      .catch(() => null);
  }

  useEffect(() => { load(); }, [statusFilter]);

  async function resolve(id: string) {
    const note = window.prompt("Resolution note:");
    if (note === null) return;
    await updateTicketStatus(id, "RESOLVED", note).catch(console.error);
    load();
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Support Tickets</h1>
        <select
          className="rounded border px-2 py-1 text-sm"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">All statuses</option>
          <option value="OPEN">Open</option>
          <option value="IN_PROGRESS">In Progress</option>
          <option value="WAITING_ON_CUSTOMER">Waiting</option>
          <option value="RESOLVED">Resolved</option>
          <option value="CLOSED">Closed</option>
        </select>
      </div>

      {sla && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Card>
            <CardHeader><CardTitle className="text-sm">Open</CardTitle></CardHeader>
            <CardContent><p className="text-2xl font-bold">{sla.openCount}</p></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-sm text-status-critical">SLA Breached</CardTitle></CardHeader>
            <CardContent><p className="text-2xl font-bold text-status-critical">{sla.slaBreachedCount}</p></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-sm">Critical Open</CardTitle></CardHeader>
            <CardContent><p className="text-2xl font-bold">{sla.criticalOpen}</p></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-sm">Avg Resolution</CardTitle></CardHeader>
            <CardContent>
              <p className="text-2xl font-bold">
                {sla.avgResolutionHours !== null ? `${sla.avgResolutionHours.toFixed(1)}h` : "—"}
              </p>
            </CardContent>
          </Card>
        </div>
      )}

      {error ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
        </Card>
      ) : tickets === null ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted">Loading…</CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader><CardTitle>{tickets.length} ticket{tickets.length !== 1 ? "s" : ""}</CardTitle></CardHeader>
          {tickets.length === 0 ? (
            <CardContent className="py-10 text-center text-sm text-muted">No tickets found.</CardContent>
          ) : (
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>Subject</TableHeaderCell>
                  <TableHeaderCell>Tenant</TableHeaderCell>
                  <TableHeaderCell>Priority</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Reporter</TableHeaderCell>
                  <TableHeaderCell>SLA Deadline</TableHeaderCell>
                  <TableHeaderCell>Actions</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {tickets.map((t) => {
                  const breached = t.slaDeadline && new Date(t.slaDeadline) < new Date()
                    && !["RESOLVED", "CLOSED"].includes(t.status);
                  return (
                    <TableRow key={t.id}>
                      <TableCell className="font-medium">{t.subject}</TableCell>
                      <TableCell className="font-mono text-xs">{t.tenantId.slice(0, 8)}…</TableCell>
                      <TableCell>
                        <Badge tone={PRIORITY_COLORS[t.priority]}>{t.priority}</Badge>
                      </TableCell>
                      <TableCell>
                        <Badge tone={STATUS_COLORS[t.status]}>{t.status}</Badge>
                      </TableCell>
                      <TableCell className="text-sm">{t.reporterEmail}</TableCell>
                      <TableCell className={`text-xs ${breached ? "text-status-critical font-semibold" : ""}`}>
                        {t.slaDeadline ? new Date(t.slaDeadline).toLocaleString() : "—"}
                        {breached ? " ⚠ BREACHED" : ""}
                      </TableCell>
                      <TableCell>
                        {!["RESOLVED", "CLOSED"].includes(t.status) && (
                          <Button size="sm" variant="outline" onClick={() => resolve(t.id)}>
                            Resolve
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </Card>
      )}
    </div>
  );
}
