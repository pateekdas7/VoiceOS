"use client";

import { use, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { ApiError, listLeads, type Lead } from "@/lib/api/client-ops";

export default function CampaignLeadsPage({ params }: { params: Promise<{ campaignId: string }> }) {
  const { campaignId } = use(params);
  const [leads, setLeads] = useState<Lead[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listLeads(campaignId)
      .then((data) => {
        if (!cancelled) setLeads(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId]);

  if (error) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
      </Card>
    );
  }
  if (leads === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading leads…</CardContent>
      </Card>
    );
  }

  return (
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
  );
}
