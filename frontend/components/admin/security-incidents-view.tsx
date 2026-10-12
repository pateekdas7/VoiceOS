"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import {
  ApiError,
  closeSecurityIncident,
  listSecurityIncidents,
  openSecurityIncident,
  type SecurityIncident,
} from "@/lib/api/admin-ops";

const SEVERITY_COLORS: Record<string, "critical" | "warning" | "brand" | "neutral"> = {
  SEV1: "critical",
  SEV2: "warning",
  SEV3: "brand",
  SEV4: "neutral",
};

const INCIDENT_TYPES = [
  "SECURITY_BREACH",
  "DATA_BREACH",
  "AI_MISBEHAVIOR",
  "UNAUTHORIZED_ACCESS",
  "COMPLIANCE_VIOLATION",
  "CREDENTIAL_COMPROMISE",
];

export function SecurityIncidentsView() {
  const [incidents, setIncidents] = useState<SecurityIncident[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    listSecurityIncidents()
      .then(setIncidents)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load incidents"));
  }

  useEffect(() => { load(); }, []);

  async function openIncident() {
    const tenantId = window.prompt("Tenant ID (leave blank for platform-wide):", "");
    if (tenantId === null) return;
    const type = window.prompt(`Incident type:\n${INCIDENT_TYPES.join("\n")}`);
    if (!type || !INCIDENT_TYPES.includes(type)) return;
    const severity = window.prompt("Severity (SEV1/SEV2/SEV3/SEV4):", "SEV2");
    if (!severity) return;
    await openSecurityIncident({ tenantId, incidentType: type, severity }).catch((e) =>
      setError(e instanceof ApiError ? e.message : "Failed to open incident")
    );
    load();
  }

  async function closeIncident(id: string) {
    const resolution = window.prompt("Resolution summary:");
    if (!resolution) return;
    await closeSecurityIncident(id, resolution).catch(console.error);
    load();
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Security Incidents</h1>
        <Button size="sm" variant="destructive" onClick={openIncident}>Open Incident</Button>
      </div>

      {error && (
        <Card>
          <CardContent className="py-4 text-sm text-status-critical">{error}</CardContent>
        </Card>
      )}

      {incidents === null ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted">Loading…</CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader><CardTitle>{incidents.length} incident{incidents.length !== 1 ? "s" : ""}</CardTitle></CardHeader>
          {incidents.length === 0 ? (
            <CardContent className="py-10 text-center text-sm text-muted">No incidents recorded.</CardContent>
          ) : (
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>Type</TableHeaderCell>
                  <TableHeaderCell>Severity</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Tenant</TableHeaderCell>
                  <TableHeaderCell>Opened</TableHeaderCell>
                  <TableHeaderCell>Notification Deadline</TableHeaderCell>
                  <TableHeaderCell>Actions</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {incidents.map((inc) => {
                  const deadlinePassed = inc.notificationDeadlineAt &&
                    new Date(inc.notificationDeadlineAt) < new Date() && inc.status !== "CLOSED";
                  return (
                    <TableRow key={inc.incidentId}>
                      <TableCell className="font-mono text-xs">{inc.incidentType.replace("_", " ")}</TableCell>
                      <TableCell>
                        <Badge tone={SEVERITY_COLORS[inc.severity]}>{inc.severity}</Badge>
                      </TableCell>
                      <TableCell>
                        <Badge tone={inc.status === "CLOSED" ? "neutral" : "critical"}>
                          {inc.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {inc.tenantId ? `${inc.tenantId.slice(0, 8)}…` : "Platform"}
                      </TableCell>
                      <TableCell className="text-xs">{new Date(inc.openedAt).toLocaleString()}</TableCell>
                      <TableCell className={`text-xs ${deadlinePassed ? "text-status-critical font-semibold" : ""}`}>
                        {inc.notificationDeadlineAt
                          ? new Date(inc.notificationDeadlineAt).toLocaleString()
                          : "—"}
                        {deadlinePassed ? " ⚠ OVERDUE" : ""}
                      </TableCell>
                      <TableCell>
                        {inc.status !== "CLOSED" && (
                          <Button size="sm" variant="outline" onClick={() => closeIncident(inc.incidentId)}>
                            Close
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
