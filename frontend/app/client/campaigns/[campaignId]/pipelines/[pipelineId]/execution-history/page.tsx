"use client";

import { use, useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";

type ExecEvent = {
  event_id: string;
  lead_id: string;
  lead_name?: string;
  lead_phone?: string;
  event_type: string;
  status: string;
  message: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

const EVENT_TONE: Record<string, "neutral" | "healthy" | "warning" | "critical"> = {
  IMPORT: "neutral", SCORED: "neutral", DISTRIBUTED: "healthy",
  QUEUED: "healthy", CALL_INITIATED: "warning", CALL_COMPLETED: "healthy",
  REJECTED: "critical", RETRY: "warning",
};

const bff = process.env.NEXT_PUBLIC_BFF_URL || "/bff";

export default function PipelineExecutionHistoryPage({
  params,
}: {
  params: Promise<{ campaignId: string; pipelineId: string }>;
}) {
  const { pipelineId } = use(params);
  const [events, setEvents] = useState<ExecEvent[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(
        `${bff}/pipelines/${pipelineId}/execution-events?limit=200`,
        { credentials: "include" }
      );
      const data = await r.json();
      setEvents(Array.isArray(data) ? data : []);
    } finally {
      setLoading(false);
    }
  }, [pipelineId]);

  useEffect(() => { load(); }, [load]);

  const filtered = events?.filter(e => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      (e.lead_name || "").toLowerCase().includes(q) ||
      (e.lead_phone || "").includes(q) ||
      e.event_type.toLowerCase().includes(q)
    );
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-sm font-semibold">Execution History</h1>
          <p className="mt-0.5 text-xs text-muted">
            Full event timeline for every lead processed by this pipeline.
          </p>
        </div>
        <button
          onClick={load}
          className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-background"
        >
          Refresh
        </button>
      </div>

      <div className="flex gap-2">
        <input
          type="search"
          placeholder="Search lead name, phone, event type…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="rounded-md border border-border bg-background px-3 py-1.5 text-sm w-64"
        />
      </div>

      <Card>
        {loading ? (
          <CardContent className="py-10 text-center text-sm text-muted">Loading…</CardContent>
        ) : !filtered || filtered.length === 0 ? (
          <CardContent className="py-14 text-center">
            <p className="text-sm font-medium">No execution events yet</p>
            <p className="mt-1 text-xs text-muted">
              Events appear here once leads are imported and processed through the pipeline.
            </p>
          </CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Lead</TableHeaderCell>
                <TableHeaderCell>Phone</TableHeaderCell>
                <TableHeaderCell>Event</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Message</TableHeaderCell>
                <TableHeaderCell>Time</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filtered.map(e => (
                <TableRow key={e.event_id}>
                  <TableCell className="font-medium">{e.lead_name || "—"}</TableCell>
                  <TableCell className="font-mono text-xs">{e.lead_phone || "—"}</TableCell>
                  <TableCell>
                    <Badge tone={EVENT_TONE[e.event_type] ?? "neutral"}>{e.event_type}</Badge>
                  </TableCell>
                  <TableCell>
                    <Badge tone={e.status === "SUCCESS" ? "healthy" : "critical"}>{e.status}</Badge>
                  </TableCell>
                  <TableCell className="text-muted text-xs max-w-xs truncate">{e.message || "—"}</TableCell>
                  <TableCell className="text-muted text-xs whitespace-nowrap">
                    {new Date(e.created_at).toLocaleString()}
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
