"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatTile } from "@/components/ui/card";

const BFF = process.env.NEXT_PUBLIC_BFF_URL || "/bff";

type WindowStats = {
  total_calls: number;
  calls_connected: number;
  calls_no_answer: number;
  calls_failed: number;
  avg_duration_s: number;
  total_duration_s: number;
  unique_leads_called: number;
  active_campaigns: number;
  answer_rate_pct: number;
  ptp_count: number;
  ptp_rate_pct: number;
  amount_collected_minor: number;
  amount_pending_minor: number;
  total_cost_minor: number;
  cost_per_connected_call_minor: number;
  cost_per_ptp_minor: number;
  callback_count: number;
  callback_rate_pct: number;
  retry_leads: number;
  retry_rate_pct: number;
};

type Overview = { "24h": WindowStats; "7d": WindowStats; "30d": WindowStats };

type DayStat = {
  day: string;
  total_calls: number;
  calls_connected: number;
  calls_no_answer: number;
  calls_failed: number;
  avg_duration_s: number;
  total_duration_s: number;
  ptp_count: number;
  answer_rate_pct: number;
  ptp_rate_pct: number;
};

type CampaignStat = {
  campaign_id: string;
  campaign_name: string;
  campaign_status: string;
  total_leads: number;
  total_calls: number;
  calls_connected: number;
  calls_no_answer: number;
  calls_failed: number;
  avg_duration_s: number;
  unique_leads_called: number;
  answer_rate_pct: number;
  ptp_count: number;
  ptp_rate_pct: number;
};

function fmt(n: number) { return n.toLocaleString("en-IN"); }
function fmtMoney(minor: number) {
  if (minor === 0) return "₹0";
  return `₹${(minor / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}
function fmtDur(s: number) {
  if (!s) return "—";
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

const WINDOWS = ["24h", "7d", "30d"] as const;
type Window = (typeof WINDOWS)[number];

export default function AnalyticsPage() {
  const [window, setWindow] = useState<Window>("30d");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [series, setSeries] = useState<DayStat[]>([]);
  const [campaigns, setCampaigns] = useState<CampaignStat[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [aggregating, setAggregating] = useState(false);
  const [aggMsg, setAggMsg] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    const days = window === "24h" ? 1 : window === "7d" ? 7 : 30;
    Promise.all([
      fetch(`${BFF}/analytics/overview`, { credentials: "include" }).then((r) => r.json()),
      fetch(`${BFF}/analytics/time-series?days=${days}`, { credentials: "include" }).then((r) => r.json()),
      fetch(`${BFF}/analytics/campaigns`, { credentials: "include" }).then((r) => r.json()),
    ])
      .then(([ov, ts, cmp]) => {
        if (ov.error) throw new Error(ov.error);
        setOverview(ov as Overview);
        setSeries(Array.isArray(ts) ? ts : []);
        setCampaigns(Array.isArray(cmp) ? cmp : []);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [window]);

  const handleExport = async () => {
    setExporting(true);
    try {
      const days = window === "24h" ? 1 : window === "7d" ? 7 : 30;
      const r = await fetch(`${BFF}/analytics/export?days=${days}&format=csv`, { credentials: "include" });
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `voiceos-analytics-${days}d.csv`; a.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  };

  const handleAggregate = async () => {
    setAggregating(true); setAggMsg(null);
    try {
      const today = new Date().toISOString().slice(0, 10);
      const r = await fetch(`${BFF}/analytics/aggregate`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ day: today }),
      });
      const data = await r.json();
      setAggMsg(data.ok ? `Aggregated ${today}` : (data.error ?? "Failed"));
    } finally {
      setAggregating(false);
    }
  };

  const w = overview?.[window];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-sm font-semibold">Analytics</h1>
          <p className="mt-0.5 text-xs text-muted">Operational metrics from live call data.</p>
        </div>
        <div className="flex items-center gap-2">
          {WINDOWS.map((w) => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              className={`rounded px-2 py-1 text-xs font-medium ${window === w ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`}
            >
              {w}
            </button>
          ))}
          <button
            onClick={handleAggregate}
            disabled={aggregating}
            className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground hover:bg-muted/80 disabled:opacity-50"
          >
            {aggregating ? "Aggregating…" : "Run Rollup"}
          </button>
          <button
            onClick={handleExport}
            disabled={exporting}
            className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground hover:bg-muted/80 disabled:opacity-50"
          >
            {exporting ? "Exporting…" : "Export CSV"}
          </button>
        </div>
      </div>

      {aggMsg && <p className="text-xs text-muted">{aggMsg}</p>}
      {loading && <p className="text-xs text-muted">Loading…</p>}
      {error && <p className="text-xs text-destructive">{error}</p>}

      {w && (
        <>
          {/* Call Volume */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile label="Total Calls"      value={fmt(w.total_calls)}         hint="All dials attempted" />
            <StatTile label="Connected"        value={fmt(w.calls_connected)}      hint="Answered by lead" />
            <StatTile label="No Answer"        value={fmt(w.calls_no_answer)}      hint="Unanswered dials" />
            <StatTile label="Failed"           value={fmt(w.calls_failed)}         hint="Carrier/network errors" />
          </div>

          {/* Rates */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile label="Answer Rate"      value={`${w.answer_rate_pct}%`}     hint="Connected / total dials" />
            <StatTile label="PTP Rate"         value={`${w.ptp_rate_pct}%`}        hint="PTPs / connected calls" />
            <StatTile label="Avg Duration"     value={fmtDur(w.avg_duration_s)}    hint="Connected calls only" />
            <StatTile label="Unique Leads"     value={fmt(w.unique_leads_called)}  hint="Distinct leads dialled" />
          </div>

          {/* Collections & Cost */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile label="PTPs Recorded"    value={fmt(w.ptp_count)}            hint="Promise-to-pay count" />
            <StatTile label="Amount Collected" value={fmtMoney(w.amount_collected_minor)} hint="Fulfilled PTPs" />
            <StatTile label="Cost / Connected" value={fmtMoney(w.cost_per_connected_call_minor)} hint="Call cost per answered call" />
            <StatTile label="Cost / PTP"       value={w.cost_per_ptp_minor > 0 ? fmtMoney(w.cost_per_ptp_minor) : "—"} hint="Cost per promise recorded" />
          </div>

          {/* Callbacks & Retries */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile label="Callbacks"        value={fmt(w.callback_count)}        hint="Leads deferred/rescheduled" />
            <StatTile label="Callback Rate"    value={`${w.callback_rate_pct}%`}    hint="Callbacks / connected calls" />
            <StatTile label="Retry Leads"      value={fmt(w.retry_leads)}           hint="Leads dialled more than once" />
            <StatTile label="Retry Rate"       value={`${w.retry_rate_pct}%`}       hint="Retry leads / unique leads called" />
          </div>
        </>
      )}

      {/* Daily Time Series */}
      {series.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Daily Breakdown</CardTitle></CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b text-muted">
                  <th className="py-1 pr-3 text-left">Date</th>
                  <th className="py-1 pr-3 text-right">Calls</th>
                  <th className="py-1 pr-3 text-right">Connected</th>
                  <th className="py-1 pr-3 text-right">Answer%</th>
                  <th className="py-1 pr-3 text-right">PTPs</th>
                  <th className="py-1 pr-3 text-right">PTP%</th>
                  <th className="py-1 pr-3 text-right">Avg Dur</th>
                </tr>
              </thead>
              <tbody>
                {[...series].reverse().map((d) => (
                  <tr key={String(d.day)} className="border-b last:border-0 hover:bg-muted/20">
                    <td className="py-1 pr-3">{String(d.day)}</td>
                    <td className="py-1 pr-3 text-right">{fmt(d.total_calls)}</td>
                    <td className="py-1 pr-3 text-right">{fmt(d.calls_connected)}</td>
                    <td className="py-1 pr-3 text-right">{d.answer_rate_pct}%</td>
                    <td className="py-1 pr-3 text-right">{fmt(d.ptp_count)}</td>
                    <td className="py-1 pr-3 text-right">{d.ptp_rate_pct}%</td>
                    <td className="py-1 pr-3 text-right">{fmtDur(d.avg_duration_s)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
      {!loading && series.length === 0 && !error && (
        <p className="text-xs text-muted">No call data yet — metrics will populate once calls are made.</p>
      )}

      {/* Per-Campaign Table */}
      {campaigns.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Campaign Breakdown</CardTitle></CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b text-muted">
                  <th className="py-1 pr-3 text-left">Campaign</th>
                  <th className="py-1 pr-3 text-right">Leads</th>
                  <th className="py-1 pr-3 text-right">Calls</th>
                  <th className="py-1 pr-3 text-right">Connected</th>
                  <th className="py-1 pr-3 text-right">Answer%</th>
                  <th className="py-1 pr-3 text-right">PTPs</th>
                  <th className="py-1 pr-3 text-right">PTP%</th>
                  <th className="py-1 pr-3 text-right">Avg Dur</th>
                  <th className="py-1 pr-3 text-right">Status</th>
                </tr>
              </thead>
              <tbody>
                {campaigns.map((c) => (
                  <tr key={c.campaign_id} className="border-b last:border-0 hover:bg-muted/20">
                    <td className="py-1 pr-3 max-w-[160px] truncate" title={c.campaign_name}>{c.campaign_name}</td>
                    <td className="py-1 pr-3 text-right">{fmt(c.total_leads)}</td>
                    <td className="py-1 pr-3 text-right">{fmt(c.total_calls)}</td>
                    <td className="py-1 pr-3 text-right">{fmt(c.calls_connected)}</td>
                    <td className="py-1 pr-3 text-right">{c.answer_rate_pct}%</td>
                    <td className="py-1 pr-3 text-right">{fmt(c.ptp_count)}</td>
                    <td className="py-1 pr-3 text-right">{c.ptp_rate_pct}%</td>
                    <td className="py-1 pr-3 text-right">{fmtDur(c.avg_duration_s)}</td>
                    <td className="py-1 pr-3 text-right text-muted capitalize">{c.campaign_status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
