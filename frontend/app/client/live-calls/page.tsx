"use client";

import { useEffect, useState, useCallback } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";

// ── Types ─────────────────────────────────────────────────────────────────────

type ActiveCall = {
  call_sid: string;
  lead_id: string;
  pipeline_id: string | null;
  phone: string;
  lead_name: string | null;
  language: string | null;
  status: string;
  started_at: string;
  answered_at: string | null;
  disposition: string | null;
  campaign_name: string | null;
};

type PipelineStat = {
  pipeline_id: string;
  name: string;
  status: string;
  calls_completed: number;
  calls_failed: number;
  calls_no_answer: number;
  total_duration_s: number;
};

type QueueStats = {
  queues: { pending: number; retry: number; callback: number; active: number };
  pipelines: PipelineStat[];
  today: {
    completed: string;
    no_answer: string;
    busy: string;
    failed: string;
    total: string;
    avg_duration_s: number | null;
  };
};

type WorkerStatus = {
  worker_id: string;
  status: string;
  active_calls: number;
  idle_pipelines: number;
  busy_pipelines: number;
  calls_today: number;
  calls_per_minute: number;
  last_heartbeat: string;
  alive: boolean;
};

// ── Helpers ───────────────────────────────────────────────────────────────────

const CALL_STATUS_TONE: Record<string, "neutral" | "healthy" | "warning" | "critical"> = {
  INITIATING: "neutral",
  RINGING:    "warning",
  IN_PROGRESS: "healthy",
  COMPLETED:  "healthy",
  NO_ANSWER:  "warning",
  BUSY:       "warning",
  FAILED:     "critical",
  TIMEOUT:    "critical",
};

const PIPELINE_STATUS_TONE: Record<string, "neutral" | "healthy" | "warning" | "critical"> = {
  IDLE: "neutral",
  BUSY: "healthy",
  PAUSED: "warning",
  ERROR: "critical",
};

function secondsSince(isoString: string): number {
  return Math.round((Date.now() - new Date(isoString).getTime()) / 1000);
}

function fmtDuration(s: number): string {
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${rem}s`;
}

// ── Live Calls Page ───────────────────────────────────────────────────────────

export default function ClientLiveCallsPage() {
  const [activeCalls,  setActiveCalls]  = useState<ActiveCall[] | null>(null);
  const [queueStats,   setQueueStats]   = useState<QueueStats | null>(null);
  const [workerStatus, setWorkerStatus] = useState<WorkerStatus[] | null>(null);
  const [ticker,       setTicker]       = useState(0); // increments every second for live duration display
  const [error,        setError]        = useState<string | null>(null);

  const bff = process.env.NEXT_PUBLIC_BFF_URL || "/bff";

  const refresh = useCallback(async () => {
    try {
      const [callsRes, statsRes, workerRes] = await Promise.all([
        fetch(`${bff}/dialer/active-calls`, { credentials: "include" }),
        fetch(`${bff}/dialer/queue-stats`,  { credentials: "include" }),
        fetch(`${bff}/dialer/status`,       { credentials: "include" }),
      ]);
      if (callsRes.ok)  setActiveCalls(await callsRes.json());
      if (statsRes.ok)  setQueueStats(await statsRes.json());
      if (workerRes.ok) setWorkerStatus(await workerRes.json());
      setError(null);
    } catch {
      setError("Failed to fetch dialer status — is the BFF running?");
    }
  }, []);

  useEffect(() => {
    refresh();
    const dataInterval  = setInterval(refresh, 5000);    // refresh data every 5s
    const tickerInterval = setInterval(() => setTicker(t => t + 1), 1000); // tick for duration
    return () => { clearInterval(dataInterval); clearInterval(tickerInterval); };
  }, [refresh]);

  const today = queueStats?.today;
  const totalToday = parseInt(today?.total || "0");
  const completedToday = parseInt(today?.completed || "0");
  const contactRate = totalToday > 0 ? Math.round((completedToday / totalToday) * 100) : 0;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Live Call Monitor</h1>
        <span className="text-xs text-muted">Auto-refreshes every 5s</span>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* ── Worker Health ─────────────────────────────────────────────── */}
      {workerStatus && workerStatus.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Dialer Worker</CardTitle></CardHeader>
          <CardContent className="flex flex-wrap gap-6">
            {workerStatus.map(w => (
              <div key={w.worker_id} className="flex flex-col gap-1">
                <div className="flex items-center gap-2">
                  <Badge tone={w.alive ? "healthy" : "critical"}>
                    {w.alive ? "ALIVE" : "OFFLINE"}
                  </Badge>
                  <span className="text-sm font-medium font-mono">{w.worker_id}</span>
                </div>
                <div className="text-xs text-muted">
                  {w.busy_pipelines} busy / {w.idle_pipelines} idle pipelines
                  &nbsp;·&nbsp;{w.calls_today} calls today
                  &nbsp;·&nbsp;{w.calls_per_minute.toFixed(1)} cpm
                </div>
                <div className="text-xs text-muted">
                  Last heartbeat: {new Date(w.last_heartbeat).toLocaleTimeString()}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* ── Queue + Today Stats ────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {[
          { label: "Pending",    value: queueStats?.queues.pending  ?? "—" },
          { label: "Active",     value: queueStats?.queues.active   ?? "—" },
          { label: "Retry",      value: queueStats?.queues.retry    ?? "—" },
          { label: "Callback",   value: queueStats?.queues.callback ?? "—" },
          { label: "Completed",  value: today?.completed  ?? "—" },
          { label: "No Answer",  value: today?.no_answer  ?? "—" },
          { label: "Contact %",  value: totalToday > 0 ? `${contactRate}%` : "—" },
          { label: "Avg Duration", value: today?.avg_duration_s ? fmtDuration(today.avg_duration_s) : "—" },
        ].map(({ label, value }) => (
          <Card key={label}>
            <CardContent className="flex flex-col gap-1 pt-4">
              <span className="text-2xl font-bold">{value}</span>
              <span className="text-xs text-muted">{label}</span>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* ── Pipeline Status ────────────────────────────────────────────── */}
      {queueStats?.pipelines && queueStats.pipelines.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Pipelines</CardTitle></CardHeader>
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Pipeline</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Completed</TableHeaderCell>
                <TableHeaderCell>No Answer</TableHeaderCell>
                <TableHeaderCell>Failed</TableHeaderCell>
                <TableHeaderCell>Total Duration</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {queueStats.pipelines.map(p => (
                <TableRow key={p.pipeline_id}>
                  <TableCell className="font-mono text-xs">{p.name || p.pipeline_id.slice(0, 8)}</TableCell>
                  <TableCell>
                    <Badge tone={PIPELINE_STATUS_TONE[p.status] ?? "neutral"}>{p.status}</Badge>
                  </TableCell>
                  <TableCell>{p.calls_completed}</TableCell>
                  <TableCell>{p.calls_no_answer}</TableCell>
                  <TableCell>{p.calls_failed}</TableCell>
                  <TableCell>{fmtDuration(p.total_duration_s)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      {/* ── Active Calls ───────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle>
            Active Calls{" "}
            {activeCalls !== null && (
              <span className="text-sm font-normal text-muted">({activeCalls.length})</span>
            )}
          </CardTitle>
        </CardHeader>
        {activeCalls === null ? (
          <CardContent className="py-10 text-center text-sm text-muted">Loading…</CardContent>
        ) : activeCalls.length === 0 ? (
          <CardContent className="py-8 text-center text-sm text-muted">
            No active calls — worker is idle or no leads in queue
          </CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Lead</TableHeaderCell>
                <TableHeaderCell>Phone</TableHeaderCell>
                <TableHeaderCell>Pipeline</TableHeaderCell>
                <TableHeaderCell>Campaign</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Duration</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {activeCalls.map(c => {
                const elapsed = secondsSince(c.started_at);
                void ticker; // re-renders each second
                return (
                  <TableRow key={c.call_sid}>
                    <TableCell className="font-medium">{c.lead_name || "—"}</TableCell>
                    <TableCell className="font-mono text-xs">{c.phone}</TableCell>
                    <TableCell className="text-xs text-muted">{c.pipeline_id?.slice(0, 8) ?? "—"}</TableCell>
                    <TableCell className="text-xs text-muted">{c.campaign_name || "—"}</TableCell>
                    <TableCell>
                      <Badge tone={CALL_STATUS_TONE[c.status] ?? "neutral"}>{c.status}</Badge>
                    </TableCell>
                    <TableCell className="text-sm tabular-nums">{fmtDuration(elapsed)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  );
}
