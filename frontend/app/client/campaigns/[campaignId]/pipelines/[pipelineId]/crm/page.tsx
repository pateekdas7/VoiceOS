"use client";

import { use, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { Breadcrumbs } from "@/components/client/breadcrumbs";

type Customer = {
  customer_id: string;
  name: string;
  external_crm_id: string | null;
  primary_contact: string | null;
  dnd: boolean;
  included_at: string;
  excluded_reason: string | null;
};

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-IN", { dateStyle: "short" });
}

const BFF = process.env.NEXT_PUBLIC_BFF_URL || "/bff";

export default function PipelineCRMPage({
  params,
}: {
  params: Promise<{ campaignId: string; pipelineId: string }>;
}) {
  const { campaignId, pipelineId } = use(params);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${BFF}/campaigns/${campaignId}/pipelines/${pipelineId}/customers`, {
      credentials: "include",
    })
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error ?? r.statusText);
        return r.json() as Promise<Customer[]>;
      })
      .then(setCustomers)
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
          { label: "CRM" },
        ]}
      />
      <div>
        <h1 className="text-sm font-semibold">Pipeline CRM</h1>
        <p className="mt-0.5 text-xs text-muted">
          Customers assigned to this pipeline&apos;s lead cohort.
        </p>
      </div>
      <Card>
        <CardHeader><CardTitle>Customers ({customers.length})</CardTitle></CardHeader>
        <CardContent>
          {loading && <p className="text-xs text-muted py-4">Loading…</p>}
          {error && <p className="text-xs text-destructive py-4">{error}</p>}
          {!loading && !error && customers.length === 0 && (
            <p className="text-xs text-muted py-4">No customers assigned to this pipeline yet.</p>
          )}
          {!loading && !error && customers.length > 0 && (
            <Table>
              <thead>
                <TableRow>
                  <TableHeaderCell>Customer</TableHeaderCell>
                  <TableHeaderCell>CRM ID</TableHeaderCell>
                  <TableHeaderCell>Contact</TableHeaderCell>
                  <TableHeaderCell>DND</TableHeaderCell>
                  <TableHeaderCell>Included</TableHeaderCell>
                </TableRow>
              </thead>
              <TableBody>
                {customers.map((c) => (
                  <TableRow key={c.customer_id}>
                    <TableCell className="font-medium">{c.name}</TableCell>
                    <TableCell>{c.external_crm_id ?? "—"}</TableCell>
                    <TableCell>{c.primary_contact ?? "—"}</TableCell>
                    <TableCell>
                      <Badge tone={c.dnd ? "critical" : "healthy"}>{c.dnd ? "DND" : "OK"}</Badge>
                    </TableCell>
                    <TableCell>{fmtDate(c.included_at)}</TableCell>
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
