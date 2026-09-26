"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { listCampaigns, type Campaign } from "@/lib/api/campaigns";
import { ApiError, listLeads, type Lead } from "@/lib/api/client-ops";

export function LeadsView() {
  const [campaigns, setCampaigns] = useState<Campaign[] | null>(null);
  const [campaignId, setCampaignId] = useState<string | null>(null);
  const [leads, setLeads] = useState<Lead[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listCampaigns().then((data) => {
      if (cancelled) return;
      setCampaigns(data);
      if (data.length > 0) setCampaignId(data[0].campaign_id);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!campaignId) return;
    let cancelled = false;
    listLeads(campaignId)
      .then((data) => {
        if (cancelled) return;
        setLeads(data);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Leads</h1>
        {campaigns === null ? (
          <span className="text-sm text-muted">Loading campaigns…</span>
        ) : campaigns.length === 0 ? (
          <span className="text-sm text-muted">No campaigns yet -- create one under Campaigns first.</span>
        ) : (
          <select
            value={campaignId ?? ""}
            onChange={(e) => setCampaignId(e.target.value)}
            className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
          >
            {campaigns.map((c) => (
              <option key={c.campaign_id} value={c.campaign_id}>
                {c.name}
              </option>
            ))}
          </select>
        )}
      </div>

      <p className="text-xs text-muted">
        &quot;Leads&quot; here is a campaign&apos;s materialized audience cohort (CampaignAudienceMember + Customer)
        -- ADR-005 does not define a separate Lead entity.
      </p>

      {error ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
        </Card>
      ) : leads === null ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted">
            {campaignId ? "Loading leads…" : "Select a campaign."}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>{leads.length} lead{leads.length === 1 ? "" : "s"}</CardTitle>
          </CardHeader>
          {leads.length === 0 ? (
            <CardContent className="py-10 text-center text-sm text-muted">
              No audience members materialized for this campaign yet.
            </CardContent>
          ) : (
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>Customer</TableHeaderCell>
                  <TableHeaderCell>Contact</TableHeaderCell>
                  <TableHeaderCell>DND</TableHeaderCell>
                  <TableHeaderCell>Included</TableHeaderCell>
                  <TableHeaderCell>Excluded</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {leads.map((l) => (
                  <TableRow key={l.campaign_audience_id}>
                    <TableCell className="font-medium">{l.customer_name ?? l.customer_id}</TableCell>
                    <TableCell className="text-muted">{l.primary_contact ?? "—"}</TableCell>
                    <TableCell>
                      <Badge tone={l.dnd ? "critical" : "healthy"}>{l.dnd ? "DND" : "OK"}</Badge>
                    </TableCell>
                    <TableCell className="text-muted">{new Date(l.included_at).toLocaleDateString()}</TableCell>
                    <TableCell className="text-muted">{l.excluded_reason || "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Card>
      )}
    </div>
  );
}
