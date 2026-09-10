"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, listAIReports, type AIReport } from "@/lib/api/admin-ops";

export function AIReportsView() {
  const [reasoningEnabled, setReasoningEnabled] = useState<boolean | null>(null);
  const [reports, setReports] = useState<AIReport[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listAIReports()
      .then((data) => {
        if (cancelled) return;
        setReasoningEnabled(data.reasoning_enabled);
        setReports(data.reports);
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
  if (reports === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading AI reports…</CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">AI Reports</h1>
        <Badge tone={reasoningEnabled ? "healthy" : "neutral"}>
          {reasoningEnabled ? "Reasoning enabled" : "Reasoning disabled"}
        </Badge>
      </div>
      {reports.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted">
            No reports generated yet from the 9-type catalog (ADR-006 §6.1).
          </CardContent>
        </Card>
      ) : (
        reports.map((r) => (
          <Card key={r.report_id}>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span>{r.report_type.replace(/_/g, " ")}</span>
                <Badge tone="neutral">{r.scope_level}</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2">
              <p className="text-sm">{r.narrative}</p>
              <p className="text-xs text-muted">{r.business_impact}</p>
              {r.recommended_actions.length > 0 ? (
                <ul className="list-inside list-disc text-xs text-muted">
                  {r.recommended_actions.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              ) : null}
              <p className="text-xs text-muted">
                {new Date(r.period_start).toLocaleDateString()} – {new Date(r.period_end).toLocaleDateString()}
              </p>
            </CardContent>
          </Card>
        ))
      )}
    </div>
  );
}
