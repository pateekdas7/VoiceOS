"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, listInsights, type Insight } from "@/lib/api/admin-ops";

const SEVERITY_TONE: Record<string, "neutral" | "healthy" | "warning" | "critical"> = {
  critical: "critical",
  warning: "warning",
  info: "neutral",
};

export function AIInsightsView() {
  const [reasoningEnabled, setReasoningEnabled] = useState<boolean | null>(null);
  const [insights, setInsights] = useState<Insight[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listInsights()
      .then((data) => {
        if (cancelled) return;
        setReasoningEnabled(data.reasoning_enabled);
        setInsights(data.insights);
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
  if (insights === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading AI insights…</CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">AI Insights</h1>
        <Badge tone={reasoningEnabled ? "healthy" : "neutral"}>
          {reasoningEnabled ? "Reasoning enabled" : "Reasoning disabled"}
        </Badge>
      </div>
      <p className="text-xs text-muted">
        Every insight below is evidence-backed (ADR-006 §3.2.2) -- each carries at least one verified fact citing a
        real Prometheus/Loki/Jaeger query, never an unevidenced model claim.
      </p>
      {!reasoningEnabled ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted">
            The ops_intelligence_reasoning flag is disabled -- enable it under Platform Settings to let the
            scheduled analysis pass generate new insights. Existing persisted insights (if any) still show below.
          </CardContent>
        </Card>
      ) : null}
      {insights.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted">No insights generated yet.</CardContent>
        </Card>
      ) : (
        insights.map((insight) => (
          <Card key={insight.insight_id}>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span>{insight.category}</span>
                <Badge tone={SEVERITY_TONE[insight.severity] ?? "neutral"}>{insight.severity}</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {insight.recommendation ? <p className="text-sm">{insight.recommendation}</p> : null}
              <div>
                <p className="text-xs font-semibold text-muted">Verified Facts</p>
                <ul className="mt-1 flex flex-col gap-1">
                  {insight.verified_facts.map((f, i) => (
                    <li key={i} className="text-xs text-muted">
                      <span className="font-mono">{f.source}</span>: {f.claim} = {f.value}
                    </li>
                  ))}
                </ul>
              </div>
              {insight.hypotheses.length > 0 ? (
                <div>
                  <p className="text-xs font-semibold text-muted">Hypotheses (inference, not fact)</p>
                  <ul className="mt-1 flex flex-col gap-1">
                    {insight.hypotheses.map((h, i) => (
                      <li key={i} className="text-xs text-muted italic">
                        {h.claim} ({h.confidence} confidence)
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <p className="text-xs text-muted">
                {insight.model} · {new Date(insight.generated_at).toLocaleString()}
              </p>
            </CardContent>
          </Card>
        ))
      )}
    </div>
  );
}
