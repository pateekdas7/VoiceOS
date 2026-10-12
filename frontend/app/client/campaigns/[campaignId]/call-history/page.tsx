"use client";

import { use, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { Breadcrumbs } from "@/components/client/breadcrumbs";
import { ApiError } from "@/lib/api/fetch-client";

type CallAttempt = {
  attempt_id: string;
  call_sid: string | null;
  lead_id: string;
  pipeline_id: string | null;
  status: string;
  disposition: string | null;
  duration_s: number | null;
  initiated_at: string;
  answered_at: string | null;
  ended_at: string | null;
  error_message: string | null;
  lead_name: string | null;
  lead_phone: string | null;
  recording_id: string | null;
  recording_state: string | null;
};

const STATUS_TONE: Record<string, "neutral" | "healthy" | "warning" | "critical"> = {
  COMPLETED: "healthy", IN_PROGRESS: "warning", INITIATED: "neutral",
  DIALING: "neutral", RINGING: "neutral", NO_ANSWER: "warning",
  BUSY: "warning", VOICEMAIL: "neutral", FAILED: "critical",
  TIMEOUT: "critical", CANCELLED: "neutral",
};

function fmtDuration(s: number | null) {
  if (s == null) return "—";
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

function fmtTime(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" });
}

const BFF = process.env.NEXT_PUBLIC_BFF_URL || "/bff";

export default function CampaignCallHistoryPage({
  params,
}: {
  params: Promise<{ campaignId: string }>;
}) {
  const { campaignId } = use(params);
  const [calls, setCalls] = useState<CallAttempt[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${BFF}/campaigns/${campaignId}/calls`, { credentials: "include" })
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error ?? r.statusText);
        return r.json() as Promise<CallAttempt[]>;
      })
      .then(setCalls)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [campaignId]);

  return (
    <div className="flex flex-col gap-6">
      <Breadcrumbs
        items={[
          { label: "Campaigns", href: "/client/campaigns" },
          { label: campaignId.slice(0, 8) + "…", href: `/client/campaigns/${campaignId}/pipelines` },
          { label: "Call History" },
        ]}
      />
      <div>
        <h1 className="text-sm font-semibold">Campaign Call History</h1>
        <p className="mt-0.5 text-xs text-muted">All outbound call attempts for this campaign.</p>
      </div>
      <Card>
        <CardHeader><CardTitle>Calls ({calls.length})</CardTitle></CardHeader>
        <CardContent>
          {loading && <p className="text-xs text-muted py-4">Loading…</p>}
          {error && <p className="text-xs text-destructive py-4">{error}</p>}
          {!loading && !error && calls.length === 0 && (
            <p className="text-xs text-muted py-4">No calls yet for this campaign.</p>
          )}
          {!loading && !error && calls.length > 0 && (
            <Table>
              <thead>
                <TableRow>
                  <TableHeaderCell>Lead</TableHeaderCell>
                  <TableHeaderCell>Phone</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Disposition</TableHeaderCell>
                  <TableHeaderCell>Duration</TableHeaderCell>
                  <TableHeaderCell>Started</TableHeaderCell>
                  <TableHeaderCell>Recording</TableHeaderCell>
                </TableRow>
              </thead>
              <TableBody>
                {calls.map((c) => (
                  <TableRow key={c.attempt_id}>
                    <TableCell className="font-medium">{c.lead_name ?? "—"}</TableCell>
                    <TableCell>{c.lead_phone ?? "—"}</TableCell>
                    <TableCell>
                      <Badge tone={STATUS_TONE[c.status] ?? "neutral"}>{c.status}</Badge>
                    </TableCell>
                    <TableCell>{c.disposition ?? "—"}</TableCell>
                    <TableCell>{fmtDuration(c.duration_s)}</TableCell>
                    <TableCell>{fmtTime(c.initiated_at)}</TableCell>
                    <TableCell>
                      {c.recording_id
                        ? <Badge tone={c.recording_state === "AVAILABLE" ? "healthy" : "neutral"}>{c.recording_state ?? "—"}</Badge>
                        : <span className="text-xs text-muted">—</span>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
