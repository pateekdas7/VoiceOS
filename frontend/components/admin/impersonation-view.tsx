"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import {
  ApiError,
  endImpersonationSession,
  getImpersonationActions,
  listImpersonationSessions,
  startImpersonationSession,
  type ImpersonationAction,
  type ImpersonationSession,
} from "@/lib/api/admin-ops";

export function ImpersonationView() {
  const [sessions, setSessions] = useState<ImpersonationSession[] | null>(null);
  const [selectedSession, setSelectedSession] = useState<string | null>(null);
  const [actions, setActions] = useState<ImpersonationAction[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeOnly, setActiveOnly] = useState(false);

  function loadSessions() {
    listImpersonationSessions(activeOnly ? { activeOnly: true } : undefined)
      .then(setSessions)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load sessions"));
  }

  useEffect(() => { loadSessions(); }, [activeOnly]);

  useEffect(() => {
    if (!selectedSession) { setActions(null); return; }
    getImpersonationActions(selectedSession).then(setActions).catch(() => setActions([]));
  }, [selectedSession]);

  async function startSession() {
    const tenantId = window.prompt("Tenant ID to impersonate:");
    if (!tenantId) return;
    const reason = window.prompt("Reason for impersonation (required for audit):");
    if (!reason) return;
    await startImpersonationSession(tenantId, reason).catch((e) =>
      setError(e instanceof ApiError ? e.message : "Failed to start session")
    );
    loadSessions();
  }

  async function endSession(id: string) {
    await endImpersonationSession(id).catch(console.error);
    loadSessions();
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Customer Impersonation</h1>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1 text-sm">
            <input type="checkbox" checked={activeOnly} onChange={(e) => setActiveOnly(e.target.checked)} />
            Active only
          </label>
          <Button size="sm" onClick={startSession}>Start Session</Button>
        </div>
      </div>

      <Card className="border-status-warning bg-status-warning/5 text-sm">
        <CardContent className="py-3">
          Every impersonation session is fully audited. All actions taken during a session are
          permanently recorded and visible to compliance and security teams.
        </CardContent>
      </Card>

      {error && (
        <Card>
          <CardContent className="py-4 text-sm text-status-critical">{error}</CardContent>
        </Card>
      )}

      {sessions === null ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted">Loading…</CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>{sessions.length} session{sessions.length !== 1 ? "s" : ""}</CardTitle>
            </CardHeader>
            {sessions.length === 0 ? (
              <CardContent className="py-10 text-center text-sm text-muted">No sessions found.</CardContent>
            ) : (
              <Table>
                <TableHead>
                  <TableRow>
                    <TableHeaderCell>Operator</TableHeaderCell>
                    <TableHeaderCell>Tenant</TableHeaderCell>
                    <TableHeaderCell>Status</TableHeaderCell>
                    <TableHeaderCell>Actions</TableHeaderCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {sessions.map((s) => (
                    <TableRow
                      key={s.id}
                      className={`cursor-pointer ${selectedSession === s.id ? "bg-muted/40" : ""}`}
                      onClick={() => setSelectedSession(s.id === selectedSession ? null : s.id)}
                    >
                      <TableCell>
                        <div className="text-sm font-medium">{s.operatorEmail}</div>
                        <div className="text-xs text-muted">{new Date(s.startedAt).toLocaleString()}</div>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{s.tenantId.slice(0, 8)}…</TableCell>
                      <TableCell>
                        <Badge tone={s.isActive ? "warning" : "neutral"}>
                          {s.isActive ? "ACTIVE" : "ENDED"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {s.isActive && (
                          <Button size="sm" variant="destructive" onClick={(e) => { e.stopPropagation(); endSession(s.id); }}>
                            End
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Card>

          {selectedSession && (
            <Card>
              <CardHeader>
                <CardTitle>Audit Trail — {actions?.length ?? "…"} action{(actions?.length ?? 0) !== 1 ? "s" : ""}</CardTitle>
              </CardHeader>
              {actions === null ? (
                <CardContent className="py-6 text-center text-sm text-muted">Loading…</CardContent>
              ) : actions.length === 0 ? (
                <CardContent className="py-6 text-center text-sm text-muted">No actions recorded yet.</CardContent>
              ) : (
                <Table>
                  <TableHead>
                    <TableRow>
                      <TableHeaderCell>Action</TableHeaderCell>
                      <TableHeaderCell>Resource</TableHeaderCell>
                      <TableHeaderCell>Time</TableHeaderCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {actions.map((a) => (
                      <TableRow key={a.id}>
                        <TableCell className="font-mono text-xs">{a.actionType}</TableCell>
                        <TableCell className="text-xs">{a.resource}</TableCell>
                        <TableCell className="text-xs">{new Date(a.performedAt).toLocaleString()}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
