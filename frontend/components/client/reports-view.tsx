"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { ApiError, listReportRuns, triggerReportRun, type ReportRun } from "@/lib/api/client-ops";

export function ReportsView() {
  const [runs, setRuns] = useState<ReportRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      setRuns(await listReportRuns());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
    }
  }

  useEffect(() => {
    let cancelled = false;
    listReportRuns()
      .then((data) => {
        if (cancelled) return;
        setRuns(data);
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

  async function handleTrigger() {
    setBusy(true);
    try {
      const today = new Date().toISOString().slice(0, 10);
      await triggerReportRun(today);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not trigger aggregation run.");
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
      </Card>
    );
  }
  if (runs === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading report run history…</CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Reports</h1>
        <button
          type="button"
          disabled={busy}
          onClick={handleTrigger}
          className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground disabled:opacity-60"
        >
          {busy ? "Running…" : "Run Today's Aggregation"}
        </button>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>{runs.length} scheduled run{runs.length === 1 ? "" : "s"}</CardTitle>
        </CardHeader>
        {runs.length === 0 ? (
          <CardContent className="py-10 text-center text-sm text-muted">
            No aggregation runs yet -- trigger one above.
          </CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Day</TableHeaderCell>
                <TableHeaderCell>Campaign</TableHeaderCell>
                <TableHeaderCell>Ran At</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {runs.map((r, i) => (
                <TableRow key={i}>
                  <TableCell className="font-medium">{r.day}</TableCell>
                  <TableCell className="text-muted">{r.campaign_id ?? "All campaigns"}</TableCell>
                  <TableCell className="text-muted">{new Date(r.ran_at).toLocaleString()}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  );
}
