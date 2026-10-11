"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatTile } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";

type Lead = {
  lead_id: string;
  campaign_id: string;
  pipeline_id: string;
  name: string;
  phone: string;
  email: string | null;
  language: string;
  score: number;
  status: string;
  queue_status: string;
  metadata: Record<string, string>;
  created_at: string;
};

type Stats = { total: string; queued: string; in_call: string; done: string; avg_score: string | null };

const STATUS_TONE: Record<string, "neutral" | "healthy" | "warning" | "critical"> = {
  NEW: "neutral", VALIDATED: "neutral", QUALIFIED: "healthy",
  ASSIGNED: "healthy", CALLED: "warning", COMPLETED: "healthy",
  REJECTED: "critical",
};
const LANG_LABELS: Record<string, string> = {
  HINDI: "HI", TAMIL: "TA", TELUGU: "TE", MARATHI: "MR",
  GUJARATI: "GU", KANNADA: "KN", MALAYALAM: "ML", BENGALI: "BN",
};

function ScoreBadge({ score }: { score: number }) {
  return <Badge tone={score >= 80 ? "healthy" : score >= 60 ? "warning" : "neutral"}>{score}</Badge>;
}

export function PipelineLeadsView({ pipelineId }: { pipelineId: string }) {
  const [leads, setLeads] = useState<Lead[] | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);

  const bff = process.env.NEXT_PUBLIC_BFF_URL || "/bff";

  const loadLeads = useCallback(async () => {
    setLoading(true);
    try {
      const params = search ? `?search=${encodeURIComponent(search)}` : "";
      const [lr, sr] = await Promise.allSettled([
        fetch(`${bff}/pipelines/${pipelineId}/leads${params}`, { credentials: "include" }).then(r => r.json()),
        fetch(`${bff}/pipelines/${pipelineId}/leads/stats`, { credentials: "include" }).then(r => r.json()),
      ]);
      if (lr.status === "fulfilled") setLeads(Array.isArray(lr.value) ? lr.value : []);
      if (sr.status === "fulfilled") setStats(sr.value);
    } finally {
      setLoading(false);
    }
  }, [pipelineId, bff, search]);

  useEffect(() => { loadLeads(); }, [loadLeads]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-sm font-semibold">Pipeline Leads</h1>
          <p className="mt-0.5 text-xs text-muted">
            Leads distributed to this pipeline by the Lead Distribution Engine. Upload happens at the campaign level.
          </p>
        </div>
        <button
          onClick={loadLeads}
          className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-background"
        >
          Refresh
        </button>
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-5 gap-3">
          <StatTile label="Total Assigned" value={stats.total ?? "0"} />
          <StatTile label="Queued" value={stats.queued ?? "0"} hint="Waiting in Redis" />
          <StatTile label="In Call" value={stats.in_call ?? "0"} hint="Active now" />
          <StatTile label="Completed" value={stats.done ?? "0"} />
          <StatTile label="Avg Score" value={stats.avg_score ? `${stats.avg_score}` : "—"} />
        </div>
      )}

      {/* Filter */}
      <div className="flex gap-2">
        <input
          type="search"
          placeholder="Search name or phone…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="rounded-md border border-border bg-background px-3 py-1.5 text-sm w-52"
        />
      </div>

      <Card>
        {loading ? (
          <CardContent className="py-10 text-center text-sm text-muted">Loading…</CardContent>
        ) : !leads || leads.length === 0 ? (
          <CardContent className="flex flex-col items-center gap-3 py-14 text-center">
            <p className="text-sm font-medium">No leads assigned to this pipeline</p>
            <p className="text-xs text-muted">
              Go to <strong>Campaign → Leads → Upload</strong> to import leads.
              The Lead Distribution Engine will assign them here based on score.
            </p>
          </CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Name</TableHeaderCell>
                <TableHeaderCell>Phone</TableHeaderCell>
                <TableHeaderCell>Language</TableHeaderCell>
                <TableHeaderCell>Score</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Queue</TableHeaderCell>
                <TableHeaderCell>Added</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {leads.map(l => (
                <TableRow key={l.lead_id}>
                  <TableCell className="font-medium">{l.name || "—"}</TableCell>
                  <TableCell className="font-mono text-sm">{l.phone}</TableCell>
                  <TableCell>
                    <span className="rounded bg-background border border-border px-1.5 py-0.5 text-xs font-mono">
                      {LANG_LABELS[l.language] || l.language}
                    </span>
                  </TableCell>
                  <TableCell><ScoreBadge score={l.score} /></TableCell>
                  <TableCell>
                    <Badge tone={STATUS_TONE[l.status] ?? "neutral"}>{l.status}</Badge>
                  </TableCell>
                  <TableCell>
                    <Badge tone={l.queue_status === "DONE" ? "healthy" : l.queue_status === "IN_CALL" ? "warning" : "neutral"}>
                      {l.queue_status}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-muted">{new Date(l.created_at).toLocaleDateString()}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  );
}
