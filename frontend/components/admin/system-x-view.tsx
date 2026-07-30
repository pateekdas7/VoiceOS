"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import {
  ApiError,
  getSystemXAuditTrail,
  getSystemXIncident,
  getSystemXNotifications,
  getSystemXRecoveryActions,
  listSystemXIncidents,
  type SystemXAuditEntry,
  type SystemXIncident,
  type SystemXIncidentDetail,
  type SystemXNotification,
  type SystemXRecoveryAction,
} from "@/lib/api/admin-ops";

const SEVERITY_COLOR: Record<string, string> = {
  CRITICAL: "text-status-critical",
  WARNING: "text-status-warning",
};

const STATUS_TONE: Record<string, "neutral" | "healthy" | "warning" | "critical"> = {
  DETECTING: "warning",
  ANALYZING: "warning",
  AWAITING_APPROVAL: "warning",
  RECOVERING: "warning",
  VERIFYING: "warning",
  ROLLING_BACK: "critical",
  RESOLVED: "healthy",
  FAILED: "critical",
};

function fmtDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

function IncidentDetail({
  incident,
  onBack,
}: {
  incident: SystemXIncidentDetail;
  onBack: () => void;
}) {
  const [actions, setActions] = useState<SystemXRecoveryAction[] | null>(null);
  const [audit, setAudit] = useState<SystemXAuditEntry[] | null>(null);
  const [notifications, setNotifications] = useState<SystemXNotification[] | null>(null);

  useEffect(() => {
    getSystemXRecoveryActions(incident.incident_id).then((r) => setActions(r.recovery_actions));
    getSystemXAuditTrail(incident.incident_id).then((r) => setAudit(r.audit_trail));
    getSystemXNotifications(incident.incident_id).then((r) => setNotifications(r.notifications));
  }, [incident.incident_id]);

  const analysis = incident.claude_analysis;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <button type="button" onClick={onBack} className="text-sm text-brand underline">
          ← All incidents
        </button>
        <h1 className={`text-lg font-semibold ${SEVERITY_COLOR[incident.severity] ?? ""}`}>
          [{incident.severity}] {incident.title}
        </h1>
        <Badge tone={STATUS_TONE[incident.status] ?? "neutral"}>{incident.status}</Badge>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Card>
          <CardContent className="pt-4">
            <p className="text-xs text-muted">Detected</p>
            <p className="text-sm font-medium">{fmtTime(incident.detected_at)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <p className="text-xs text-muted">Resolved</p>
            <p className="text-sm font-medium">{incident.resolved_at ? fmtTime(incident.resolved_at) : "—"}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <p className="text-xs text-muted">Downtime</p>
            <p className="text-sm font-medium">{fmtDuration(incident.total_downtime_s)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <p className="text-xs text-muted">Services</p>
            <p className="text-sm font-medium">{incident.affected_services.join(", ") || "—"}</p>
          </CardContent>
        </Card>
      </div>

      {analysis && (
        <Card>
          <CardHeader>
            <CardTitle>Claude Analysis</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div>
              <p className="text-xs font-semibold text-muted uppercase tracking-wide">Root Cause ({analysis.confidence} confidence)</p>
              <p className="text-sm mt-1">{analysis.root_cause}</p>
            </div>
            <div>
              <p className="text-xs font-semibold text-muted uppercase tracking-wide">Recovery Plan</p>
              <ol className="list-decimal list-inside text-sm mt-1 space-y-1">
                {analysis.recovery_plan.map((step, i) => (
                  <li key={i}>{step}</li>
                ))}
              </ol>
            </div>
            <div>
              <p className="text-xs font-semibold text-muted uppercase tracking-wide">Risk Assessment</p>
              <p className="text-sm mt-1">{analysis.risk_assessment}</p>
            </div>
            <p className="text-xs text-muted">
              Model: {analysis.model} · Est. recovery: {analysis.estimated_recovery_time_s}s
            </p>
          </CardContent>
        </Card>
      )}

      {incident.root_cause && (
        <Card>
          <CardHeader>
            <CardTitle>Root Cause & Recovery Summary</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <p className="text-sm">{incident.root_cause}</p>
            {incident.recovery_summary && (
              <p className="text-sm text-muted">{incident.recovery_summary}</p>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Recovery Actions</CardTitle>
        </CardHeader>
        {!actions ? (
          <CardContent className="py-6 text-center text-sm text-muted">Loading…</CardContent>
        ) : actions.length === 0 ? (
          <CardContent className="py-6 text-center text-sm text-muted">No actions recorded.</CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Action</TableHeaderCell>
                <TableHeaderCell>Service</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Result / Error</TableHeaderCell>
                <TableHeaderCell>Started</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {actions.map((a) => (
                <TableRow key={a.action_id}>
                  <TableCell className="font-medium">{a.action_type}</TableCell>
                  <TableCell>{a.target_service ?? "—"}</TableCell>
                  <TableCell>
                    <Badge
                      tone={
                        a.status === "COMPLETED" ? "healthy" : a.status === "FAILED" ? "critical" : "warning"
                      }
                    >
                      {a.status}
                    </Badge>
                  </TableCell>
                  <TableCell className="max-w-xs truncate text-xs">
                    {a.result ?? a.error ?? "—"}
                  </TableCell>
                  <TableCell className="text-xs text-muted">{fmtTime(a.started_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Notifications</CardTitle>
        </CardHeader>
        {!notifications ? (
          <CardContent className="py-6 text-center text-sm text-muted">Loading…</CardContent>
        ) : notifications.length === 0 ? (
          <CardContent className="py-6 text-center text-sm text-muted">No notifications sent.</CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Channel</TableHeaderCell>
                <TableHeaderCell>Type</TableHeaderCell>
                <TableHeaderCell>Recipient</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Sent</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {notifications.map((n) => (
                <TableRow key={n.notification_id}>
                  <TableCell className="font-medium uppercase">{n.channel}</TableCell>
                  <TableCell>{n.notification_type}</TableCell>
                  <TableCell className="text-xs">{n.recipient}</TableCell>
                  <TableCell>
                    <Badge tone={n.status === "sent" ? "healthy" : n.status === "failed" ? "critical" : "neutral"}>
                      {n.status}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-xs text-muted">{n.sent_at ? fmtTime(n.sent_at) : "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Audit Trail</CardTitle>
        </CardHeader>
        {!audit ? (
          <CardContent className="py-6 text-center text-sm text-muted">Loading…</CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Time</TableHeaderCell>
                <TableHeaderCell>Actor</TableHeaderCell>
                <TableHeaderCell>Action</TableHeaderCell>
                <TableHeaderCell>Result</TableHeaderCell>
                <TableHeaderCell>Verification</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {audit.map((e) => (
                <TableRow key={e.entry_id}>
                  <TableCell className="text-xs text-muted whitespace-nowrap">{fmtTime(e.recorded_at)}</TableCell>
                  <TableCell className="text-xs">{e.actor}</TableCell>
                  <TableCell className="font-mono text-xs">{e.action}</TableCell>
                  <TableCell className="text-xs">{e.result ?? "—"}</TableCell>
                  <TableCell className="text-xs">{e.verification_outcome ?? "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  );
}

export function SystemXView() {
  const [incidents, setIncidents] = useState<SystemXIncident[] | null>(null);
  const [selected, setSelected] = useState<SystemXIncidentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeOnly, setActiveOnly] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listSystemXIncidents(activeOnly)
      .then((r) => { if (!cancelled) setIncidents(r.incidents); })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not load incidents."); });
    return () => { cancelled = true; };
  }, [activeOnly]);

  async function openIncident(id: string) {
    setLoadingDetail(true);
    try {
      const r = await getSystemXIncident(id);
      setSelected(r.incident);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load incident.");
    } finally {
      setLoadingDetail(false);
    }
  }

  if (selected) {
    return <IncidentDetail incident={selected} onBack={() => setSelected(null)} />;
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">System X — Autonomous Operations</h1>
          <p className="text-xs text-muted">
            Every Alertmanager alert is autonomously classified, correlated, diagnosed by Claude, recovered, and notified.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={activeOnly}
            onChange={(e) => setActiveOnly(e.target.checked)}
            className="rounded"
          />
          Active only
        </label>
      </div>

      {error && (
        <Card>
          <CardContent className="py-8 text-center text-sm text-status-critical">{error}</CardContent>
        </Card>
      )}

      {!incidents ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted">Loading incidents…</CardContent>
        </Card>
      ) : incidents.length === 0 ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted">
            {activeOnly ? "No active incidents." : "No incidents recorded yet."}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>{incidents.length} incident{incidents.length === 1 ? "" : "s"}</CardTitle>
          </CardHeader>
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Severity</TableHeaderCell>
                <TableHeaderCell>Title</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Services</TableHeaderCell>
                <TableHeaderCell>Detected</TableHeaderCell>
                <TableHeaderCell>Downtime</TableHeaderCell>
                <TableHeaderCell></TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {incidents.map((inc) => (
                <TableRow key={inc.incident_id}>
                  <TableCell className={`font-semibold ${SEVERITY_COLOR[inc.severity] ?? ""}`}>
                    {inc.severity}
                  </TableCell>
                  <TableCell className="font-medium max-w-xs truncate">{inc.title}</TableCell>
                  <TableCell>
                    <Badge tone={STATUS_TONE[inc.status] ?? "neutral"}>{inc.status}</Badge>
                  </TableCell>
                  <TableCell className="text-xs">{inc.affected_services.join(", ") || "—"}</TableCell>
                  <TableCell className="text-xs text-muted whitespace-nowrap">{fmtTime(inc.detected_at)}</TableCell>
                  <TableCell className="text-xs">{fmtDuration(inc.total_downtime_s)}</TableCell>
                  <TableCell>
                    <button
                      type="button"
                      disabled={loadingDetail}
                      onClick={() => openIncident(inc.incident_id)}
                      className="text-sm text-brand underline disabled:opacity-50"
                    >
                      View
                    </button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </div>
  );
}
