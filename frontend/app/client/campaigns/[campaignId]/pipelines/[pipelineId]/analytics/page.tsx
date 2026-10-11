"use client";

import { use, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatTile } from "@/components/ui/card";
import { Breadcrumbs } from "@/components/client/breadcrumbs";

type PipelineAnalytics = {
  calls_completed: number;
  calls_no_answer: number;
  calls_failed: number;
  total_duration_s: number;
  avg_duration_s: number;
  contact_rate_pct: number;
  ptp_count: number;
  ptp_rate_pct: number;
  unique_leads: number;
  total_events: number;
  successes: number;
  failures: number;
  skipped: number;
};

function fmtDuration(s: number): string {
  if (s === 0) return "—";
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

const BFF = process.env.NEXT_PUBLIC_BFF_URL || "/bff";

export default function PipelineAnalyticsPage({
  params,
}: {
  params: Promise<{ campaignId: string; pipelineId: string }>;
}) {
  const { campaignId, pipelineId } = use(params);
  const [data, setData] = useState<PipelineAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${BFF}/campaigns/${campaignId}/pipelines/${pipelineId}/analytics`, {
      credentials: "include",
    })
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error ?? r.statusText);
        return r.json() as Promise<PipelineAnalytics>;
      })
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [campaignId, pipelineId]);

  return (
    <div className="flex flex-col gap-6">
      <Breadcrumbs
        items={[
          { label: "Campaigns", href: "/client/campaigns" },
          { label: campaignId.slice(0, 8) + "…", href: `/client/campaigns/${campaignId}/pipelines` },
          { label: pipelineId.slice(0, 8) + "…", href: `/client/campaigns/${campaignId}/pipelines/${pipelineId}/leads` },
          { label: "Analytics" },
        ]}
      />
      <div>
        <h1 className="text-sm font-semibold">Pipeline Analytics</h1>
        <p className="mt-0.5 text-xs text-muted">Live execution metrics from this pipeline.</p>
      </div>

      {loading && <p className="text-xs text-muted">Loading…</p>}
      {error && <p className="text-xs text-destructive">{error}</p>}

      {!loading && !error && data && (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <StatTile label="Calls Connected"    value={String(data.calls_completed)}   hint="Answered by lead" />
            <StatTile label="No Answer"          value={String(data.calls_no_answer)}   hint="Unanswered dials" />
            <StatTile label="Failed"             value={String(data.calls_failed)}      hint="Carrier/network errors" />
            <StatTile label="Contact Rate"       value={`${data.contact_rate_pct}%`}    hint="Connected / total dials" />
          </div>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <StatTile label="PTPs Recorded"      value={String(data.ptp_count)}         hint="Promise-to-pay commitments" />
            <StatTile label="PTP Rate"           value={`${data.ptp_rate_pct}%`}        hint="PTPs / unique leads" />
            <StatTile label="Avg Call Duration"  value={fmtDuration(data.avg_duration_s)} hint="Connected calls only" />
            <StatTile label="Unique Leads"       value={String(data.unique_leads)}      hint="Distinct leads touched" />
          </div>

          <Card>
            <CardHeader><CardTitle>Execution Events</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-3 gap-4">
              <StatTile label="Total Events"  value={String(data.total_events)}  hint="All pipeline events" />
              <StatTile label="Successes"     value={String(data.successes)}     hint="SUCCESS status events" />
              <StatTile label="Failures"      value={String(data.failures)}      hint="FAILURE status events" />
            </CardContent>
          </Card>

          {data.unique_leads === 0 && (
            <p className="text-xs text-muted">
              No execution data yet — metrics will populate once this pipeline starts processing leads.
            </p>
          )}
        </>
      )}
    </div>
  );
}
