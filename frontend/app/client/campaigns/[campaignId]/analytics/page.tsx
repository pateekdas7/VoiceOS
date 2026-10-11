"use client";

import { use, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatTile } from "@/components/ui/card";
import { Breadcrumbs } from "@/components/client/breadcrumbs";

const BFF = process.env.NEXT_PUBLIC_BFF_URL || "/bff";

type CampaignAnalytics = {
  total_calls: number;
  calls_connected: number;
  calls_no_answer: number;
  calls_failed: number;
  total_leads: number;
  unique_leads_called: number;
  avg_duration_s: number;
  ptp_count: number;
  ptp_rate: number;
  ptp_rate_pct: number;
  contactability_rate: number;
  conversion_rate: number;
  answer_rate_pct: number;
  amount_collected_minor: number;
  callback_count: number;
  callback_rate_pct: number;
  retry_leads: number;
  retry_rate_pct: number;
};

function fmtDur(s: number) {
  if (!s) return "—";
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

function fmtMoney(minor: number) {
  if (!minor) return "₹0";
  return `₹${(minor / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

export default function CampaignAnalyticsPage({
  params,
}: {
  params: Promise<{ campaignId: string }>;
}) {
  const { campaignId } = use(params);
  const [data, setData] = useState<CampaignAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${BFF}/analytics/campaigns/${campaignId}/summary`, { credentials: "include" })
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error ?? r.statusText);
        return r.json() as Promise<CampaignAnalytics>;
      })
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [campaignId]);

  return (
    <div className="flex flex-col gap-6">
      <Breadcrumbs
        items={[
          { label: "Campaigns", href: "/client/campaigns" },
          { label: campaignId.slice(0, 8) + "…", href: `/client/campaigns/${campaignId}/leads` },
          { label: "Analytics" },
        ]}
      />
      <div>
        <h1 className="text-sm font-semibold">Campaign Analytics</h1>
        <p className="mt-0.5 text-xs text-muted">Real-time call performance for this campaign.</p>
      </div>

      {loading && <p className="text-xs text-muted">Loading…</p>}
      {error && <p className="text-xs text-destructive">{error}</p>}

      {!loading && !error && data && (
        <>
          {/* Lead Coverage */}
          <Card>
            <CardHeader><CardTitle>Lead Coverage</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <StatTile label="Total Leads"     value={String(data.total_leads)}          hint="Leads in this campaign" />
              <StatTile label="Leads Reached"   value={String(data.unique_leads_called)}  hint="Distinct leads dialled" />
              <StatTile label="Coverage"
                value={data.total_leads > 0
                  ? `${Math.round((data.unique_leads_called / data.total_leads) * 100)}%`
                  : "—"}
                hint="Leads reached / total leads"
              />
              <StatTile label="Total Calls"     value={String(data.total_calls)}          hint="All dial attempts" />
            </CardContent>
          </Card>

          {/* Call Outcomes */}
          <Card>
            <CardHeader><CardTitle>Call Outcomes</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <StatTile label="Connected"       value={String(data.calls_connected)}      hint="Answered by lead" />
              <StatTile label="No Answer"       value={String(data.calls_no_answer)}      hint="Unanswered dials" />
              <StatTile label="Failed"          value={String(data.calls_failed)}         hint="Carrier/network errors" />
              <StatTile label="Answer Rate"     value={`${data.answer_rate_pct}%`}        hint="Connected / total dials" />
            </CardContent>
          </Card>

          {/* Collections */}
          <Card>
            <CardHeader><CardTitle>Collections Performance</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <StatTile label="PTPs Recorded"   value={String(data.ptp_count)}            hint="Promise-to-pay commitments" />
              <StatTile label="PTP Rate"        value={`${data.ptp_rate_pct}%`}           hint="PTPs / connected calls" />
              <StatTile label="Contactability"  value={`${(data.contactability_rate * 100).toFixed(1)}%`} hint="Connected / total dials" />
              <StatTile label="Amount Collected" value={fmtMoney(data.amount_collected_minor)} hint="Fulfilled PTPs" />
            </CardContent>
          </Card>

          {/* Efficiency */}
          <Card>
            <CardHeader><CardTitle>Call Efficiency</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              <StatTile label="Avg Duration"    value={fmtDur(data.avg_duration_s)}       hint="Connected calls only" />
              <StatTile label="Conversion Rate" value={`${(data.conversion_rate * 100).toFixed(1)}%`} hint="PTPs / total calls" />
              <StatTile label="PTP per 100"
                value={data.calls_connected > 0
                  ? String(Math.round((data.ptp_count / data.calls_connected) * 100))
                  : "—"}
                hint="PTPs per 100 connected calls"
              />
            </CardContent>
          </Card>

          {/* Callbacks & Retries */}
          <Card>
            <CardHeader><CardTitle>Callbacks &amp; Retries</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <StatTile label="Callbacks"      value={String(data.callback_count)}    hint="Leads deferred/rescheduled" />
              <StatTile label="Callback Rate"  value={`${data.callback_rate_pct}%`}   hint="Callbacks / connected calls" />
              <StatTile label="Retry Leads"    value={String(data.retry_leads)}       hint="Leads dialled more than once" />
              <StatTile label="Retry Rate"     value={`${data.retry_rate_pct}%`}      hint="Retry leads / unique leads called" />
            </CardContent>
          </Card>

          {data.total_calls === 0 && (
            <p className="text-xs text-muted">
              No calls made yet — metrics will populate once this campaign starts dialling.
            </p>
          )}
        </>
      )}
    </div>
  );
}
