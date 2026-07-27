"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { listPipelines, type LocalPipeline } from "@/lib/local-pipelines";

const STATUS_TONE: Record<string, "neutral" | "healthy" | "warning" | "critical"> = {
  DRAFT: "neutral",
  ACTIVE: "healthy",
  PAUSED: "warning",
  ARCHIVED: "neutral",
};

export default function PipelinesPage({ params }: { params: Promise<{ campaignId: string }> }) {
  const { campaignId } = use(params);
  const [pipelines, setPipelines] = useState<LocalPipeline[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    listPipelines(campaignId).then((data) => {
      if (!cancelled) setPipelines(data);
    });
    return () => {
      cancelled = true;
    };
  }, [campaignId]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Pipelines</h1>
        <Link
          href={`/client/campaigns/${campaignId}/pipelines/new`}
          className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground"
        >
          Create Pipeline
        </Link>
      </div>
      <p className="text-xs text-muted">
        Pipeline execution (leads processing, per-pipeline model config, run history) isn&apos;t backed by a real
        service yet -- ADR-005 §4.3/§14 scopes that as its own dedicated sprint. Pipelines created here are real and
        persist in this browser, ready to be handed to a real backend once it exists.
      </p>
      <Card>
        <CardHeader>
          <CardTitle>{pipelines === null ? "…" : pipelines.length} pipeline{pipelines?.length === 1 ? "" : "s"}</CardTitle>
        </CardHeader>
        {pipelines === null ? (
          <CardContent className="py-10 text-center text-sm text-muted">Loading…</CardContent>
        ) : pipelines.length === 0 ? (
          <CardContent className="py-10 text-center text-sm text-muted">
            No pipelines yet -- create one above.
          </CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Pipeline</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Created</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {pipelines.map((p) => (
                <TableRow key={p.pipeline_id}>
                  <TableCell className="font-medium">
                    <Link
                      href={`/client/campaigns/${campaignId}/pipelines/${p.pipeline_id}`}
                      className="text-brand hover:underline"
                    >
                      {p.name}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <Badge tone={STATUS_TONE[p.status] ?? "neutral"}>{p.status}</Badge>
                  </TableCell>
                  <TableCell className="text-muted">{new Date(p.created_at).toLocaleDateString()}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  );
}
