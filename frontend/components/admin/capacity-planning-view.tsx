"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { ApiError, listCapacityForecasts, type CapacityForecast } from "@/lib/api/admin-ops";

const RESOURCES = ["gpu", "postgres_connections", "redis_memory"];

export function CapacityPlanningView() {
  const [resource, setResource] = useState(RESOURCES[0]);
  const [reasoningEnabled, setReasoningEnabled] = useState<boolean | null>(null);
  const [forecasts, setForecasts] = useState<CapacityForecast[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listCapacityForecasts(resource)
      .then((data) => {
        if (cancelled) return;
        setReasoningEnabled(data.reasoning_enabled);
        setForecasts(data.forecasts);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
      });
    return () => {
      cancelled = true;
    };
  }, [resource]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Capacity Planning</h1>
        <div className="flex items-center gap-3">
          <Badge tone={reasoningEnabled ? "healthy" : "neutral"}>
            {reasoningEnabled ? "Reasoning enabled" : "Reasoning disabled"}
          </Badge>
          <select
            value={resource}
            onChange={(e) => setResource(e.target.value)}
            className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
          >
            {RESOURCES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
        </Card>
      ) : forecasts === null ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted">Loading forecasts…</CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Forecast history: {resource}</CardTitle>
          </CardHeader>
          {forecasts.length === 0 ? (
            <CardContent className="py-10 text-center text-sm text-muted">
              No capacity forecasts generated yet for this resource.
            </CardContent>
          ) : (
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>Horizon</TableHeaderCell>
                  <TableHeaderCell>Headroom</TableHeaderCell>
                  <TableHeaderCell>Confidence</TableHeaderCell>
                  <TableHeaderCell>Generated</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {forecasts.map((f) => (
                  <TableRow key={f.forecast_id}>
                    <TableCell className="font-medium">{f.horizon_days}d</TableCell>
                    <TableCell className="text-muted">{f.headroom_pct.toFixed(1)}%</TableCell>
                    <TableCell className="text-muted">{f.confidence}</TableCell>
                    <TableCell className="text-muted">{new Date(f.generated_at).toLocaleString()}</TableCell>
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
