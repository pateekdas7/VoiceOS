"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, StatTile } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { ApiError, getGpuFleetHealth, type GPUFleetHealth } from "@/lib/api/admin-ops";

export function InfrastructureView() {
  const [health, setHealth] = useState<GPUFleetHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getGpuFleetHealth()
      .then((data) => {
        if (cancelled) return;
        setHealth(data);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
      </Card>
    );
  }
  if (health === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading GPU fleet health…</CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">Infrastructure</h1>
      <div className="grid grid-cols-3 gap-4">
        <StatTile label="Fleet Health Score" value={`${(health.fleet_health_score * 100).toFixed(0)}%`} />
        <StatTile label="Degraded" value={health.is_degraded ? "Yes" : "No"} />
        <StatTile label="Severely Degraded" value={health.is_severely_degraded ? "Yes" : "No"} />
      </div>
      <Card>
        <CardHeader>
          <CardTitle>GPU Nodes ({health.nodes.length})</CardTitle>
        </CardHeader>
        {health.nodes.length === 0 ? (
          <CardContent className="py-10 text-center text-sm text-muted">
            No GPU nodes currently reporting in -- this environment&apos;s single GPU node
            (GPU_NODE_STATE.md) has no live health-reporting loop feeding this monitor.
            Real once a node calls report_node().
          </CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Node</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>VRAM Used</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {health.nodes.map((n) => (
                <TableRow key={n.node_id}>
                  <TableCell className="font-medium">{n.node_id}</TableCell>
                  <TableCell>
                    <Badge tone={n.healthy ? "healthy" : "critical"}>{n.healthy ? "Healthy" : "Unhealthy"}</Badge>
                  </TableCell>
                  <TableCell className="text-muted">
                    {n.vram_used_mb} / {n.vram_total_mb} MB
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  );
}
