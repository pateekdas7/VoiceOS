"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { acknowledgeAlert, ApiError, listAlerts, resolveAlert, type AlertRecord } from "@/lib/api/admin-ops";

const SEVERITY_TONE: Record<string, "neutral" | "healthy" | "warning" | "critical"> = {
  critical: "critical",
  warning: "warning",
  info: "neutral",
};

export function AlertsView() {
  const [alerts, setAlerts] = useState<AlertRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function refresh() {
    try {
      setAlerts(await listAlerts());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
    }
  }

  useEffect(() => {
    let cancelled = false;
    listAlerts()
      .then((data) => {
        if (cancelled) return;
        setAlerts(data);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handle(action: "ack" | "resolve", id: string) {
    setBusyId(id);
    try {
      if (action === "ack") await acknowledgeAlert(id);
      else await resolveAlert(id);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Action failed.");
    } finally {
      setBusyId(null);
    }
  }

  if (error) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
      </Card>
    );
  }
  if (alerts === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading alerts…</CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">Alerts Center</h1>
      <p className="text-xs text-muted">
        The always-on alert lifecycle (ADR-006 §4) -- reuses the existing Alertmanager/ComplianceMonitoring alert
        sources, never a parallel alerting system.
      </p>
      <Card>
        <CardHeader>
          <CardTitle>{alerts.length} open alert{alerts.length === 1 ? "" : "s"}</CardTitle>
        </CardHeader>
        {alerts.length === 0 ? (
          <CardContent className="py-10 text-center text-sm text-muted">No open alerts.</CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Source</TableHeaderCell>
                <TableHeaderCell>Severity</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Fired</TableHeaderCell>
                <TableHeaderCell>Actions</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {alerts.map((a) => (
                <TableRow key={a.alert_id}>
                  <TableCell className="font-medium">{a.source}</TableCell>
                  <TableCell>
                    <Badge tone={SEVERITY_TONE[a.severity] ?? "neutral"}>{a.severity}</Badge>
                  </TableCell>
                  <TableCell className="text-muted">{a.status}</TableCell>
                  <TableCell className="text-muted">{new Date(a.fired_at).toLocaleString()}</TableCell>
                  <TableCell>
                    <div className="flex gap-3">
                      {a.status === "firing" ? (
                        <button
                          type="button"
                          disabled={busyId === a.alert_id}
                          onClick={() => handle("ack", a.alert_id)}
                          className="text-sm text-brand underline disabled:opacity-50"
                        >
                          Acknowledge
                        </button>
                      ) : null}
                      <button
                        type="button"
                        disabled={busyId === a.alert_id}
                        onClick={() => handle("resolve", a.alert_id)}
                        className="text-sm text-brand underline disabled:opacity-50"
                      >
                        Resolve
                      </button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  );
}
