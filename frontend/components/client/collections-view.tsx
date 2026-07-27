"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { ApiError, listEscalations, resolveEscalation, type Escalation } from "@/lib/api/client-ops";

export function CollectionsView() {
  const [escalations, setEscalations] = useState<Escalation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});

  async function refresh() {
    try {
      setEscalations(await listEscalations());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
    }
  }

  useEffect(() => {
    let cancelled = false;
    listEscalations()
      .then((data) => {
        if (cancelled) return;
        setEscalations(data);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleResolve(id: string) {
    const resolutionNotes = notes[id] ?? "";
    setBusyId(id);
    try {
      await resolveEscalation(id, resolutionNotes);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not resolve escalation.");
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
  if (escalations === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading escalations…</CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">Collections</h1>
      <Card>
        <CardHeader>
          <CardTitle>{escalations.length} escalation{escalations.length === 1 ? "" : "s"}</CardTitle>
        </CardHeader>
        {escalations.length === 0 ? (
          <CardContent className="py-10 text-center text-sm text-muted">No escalations recorded yet.</CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Call</TableHeaderCell>
                <TableHeaderCell>Reason</TableHeaderCell>
                <TableHeaderCell>Escalated To</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Resolve</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {escalations.map((e) => (
                <TableRow key={e.escalation_id}>
                  <TableCell className="font-medium">{e.call_id}</TableCell>
                  <TableCell className="text-muted">{e.reason}</TableCell>
                  <TableCell className="text-muted">{e.escalated_to}</TableCell>
                  <TableCell>
                    <Badge tone={e.resolved_at ? "healthy" : "warning"}>{e.resolved_at ? "Resolved" : "Open"}</Badge>
                  </TableCell>
                  <TableCell>
                    {e.resolved_at ? (
                      <span className="text-sm text-muted">—</span>
                    ) : (
                      <div className="flex gap-2">
                        <input
                          value={notes[e.escalation_id] ?? ""}
                          onChange={(ev) => setNotes((n) => ({ ...n, [e.escalation_id]: ev.target.value }))}
                          placeholder="Resolution notes"
                          className="min-w-[10rem] rounded-md border border-border bg-surface px-2 py-1 text-xs"
                        />
                        <button
                          type="button"
                          disabled={busyId === e.escalation_id}
                          onClick={() => handleResolve(e.escalation_id)}
                          className="text-sm text-brand underline disabled:opacity-50"
                        >
                          Resolve
                        </button>
                      </div>
                    )}
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
