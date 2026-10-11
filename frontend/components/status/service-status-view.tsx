"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Component = { name: string; status: string };
type StatusReport = { status: string; components: Component[]; updatedAt: string };

const STATUS_COLORS: Record<string, string> = {
  healthy: "text-status-ok",
  degraded: "text-status-warning",
  unhealthy: "text-status-critical",
  unknown: "text-muted",
};

const STATUS_DOT: Record<string, string> = {
  healthy: "bg-status-ok",
  degraded: "bg-status-warning",
  unhealthy: "bg-status-critical",
  unknown: "bg-muted",
};

export function ServiceStatusView() {
  const [report, setReport] = useState<StatusReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL ?? "";
    fetch(`${BFF_URL}/status`)
      .then((r) => r.json())
      .then(setReport)
      .catch(() => setError("Could not reach the status endpoint."));

    const interval = setInterval(() => {
      fetch(`${BFF_URL}/status`)
        .then((r) => r.json())
        .then(setReport)
        .catch(() => null);
    }, 30_000);
    return () => clearInterval(interval);
  }, []);

  const overallColor = report
    ? report.status === "operational"
      ? "text-status-ok"
      : "text-status-warning"
    : "text-muted";

  return (
    <div className="mx-auto max-w-2xl py-12 px-4 flex flex-col gap-6">
      <div className="text-center">
        <h1 className="text-2xl font-bold mb-1">VoiceOS Platform Status</h1>
        {report && (
          <p className={`text-lg font-semibold capitalize ${overallColor}`}>
            {report.status === "operational" ? "All systems operational" : "Service degradation detected"}
          </p>
        )}
        {report && (
          <p className="text-xs text-muted mt-1">
            Updated {new Date(report.updatedAt).toLocaleTimeString()} · auto-refreshes every 30s
          </p>
        )}
      </div>

      {error ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
        </Card>
      ) : report === null ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted">Checking status…</CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader><CardTitle>Components</CardTitle></CardHeader>
          <CardContent>
            <ul className="divide-y">
              {report.components.map((c) => (
                <li key={c.name} className="flex items-center justify-between py-3">
                  <span className="font-medium capitalize">{c.name.replace(/_/g, " ")}</span>
                  <span className={`flex items-center gap-2 text-sm font-semibold capitalize ${STATUS_COLORS[c.status] ?? "text-muted"}`}>
                    <span className={`inline-block h-2 w-2 rounded-full ${STATUS_DOT[c.status] ?? "bg-muted"}`} />
                    {c.status}
                  </span>
                </li>
              ))}
              {report.components.length === 0 && (
                <li className="py-6 text-center text-sm text-muted">No components reported.</li>
              )}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
