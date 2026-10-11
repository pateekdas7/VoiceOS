"use client";

import { useEffect, useState } from "react";
import { HealthBadge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { toHealthStatus } from "@/lib/health";
import type { HealthComponent } from "@/lib/types";

// Reuses the existing Grafana deployment (docker-compose's `grafana` service,
// or the K8s-deployed instance in staging/prod) via a direct link + best-effort
// iframe -- never a parallel monitoring UI. In local dev, Grafana's default
// compose port (3000) collides with the Next.js dev server's own port, so the
// URL is env-configurable rather than assumed.
export function SystemHealthView() {
  const [data, setData] = useState<HealthComponent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [grafanaReachable, setGrafanaReachable] = useState<boolean | null>(null);
  const bffUrl = process.env.NEXT_PUBLIC_BFF_URL;
  const grafanaUrl = process.env.NEXT_PUBLIC_GRAFANA_URL;

  useEffect(() => {
    if (!bffUrl) return;
    let cancelled = false;
    fetch(`${bffUrl}/system/health`)
      .then((res) => {
        if (!res.ok) throw new Error(`status ${res.status}`);
        return res.json();
      })
      .then((json: HealthComponent[]) => {
        if (!cancelled) setData(json);
      })
      .catch(() => {
        if (!cancelled) setError("Could not reach the backend health endpoint.");
      });
    return () => {
      cancelled = true;
    };
  }, [bffUrl]);

  useEffect(() => {
    if (!grafanaUrl) return;
    let cancelled = false;
    fetch(`${grafanaUrl}/api/health`, { mode: "no-cors" })
      .then(() => {
        if (!cancelled) setGrafanaReachable(true);
      })
      .catch(() => {
        if (!cancelled) setGrafanaReachable(false);
      });
    return () => {
      cancelled = true;
    };
  }, [grafanaUrl]);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">System Health</h1>

      <Card>
        <CardHeader>
          <CardTitle>Backend components</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {!bffUrl ? (
            <p className="text-sm text-status-critical">NEXT_PUBLIC_BFF_URL is not configured.</p>
          ) : error ? (
            <p className="text-sm text-status-critical">{error}</p>
          ) : data === null ? (
            <p className="text-sm text-muted">Loading…</p>
          ) : data.length === 0 ? (
            <p className="text-sm text-muted">No health checks registered.</p>
          ) : (
            data.map((c) => (
              <HealthBadge key={c.component} component={c.component} status={toHealthStatus(c.status)} />
            ))
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Grafana dashboards</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {!grafanaUrl ? (
            <p className="text-sm text-muted">
              NEXT_PUBLIC_GRAFANA_URL is not configured for this environment. Set it to the deployed Grafana
              instance&apos;s URL (e.g. the docker-compose <code>grafana</code> service, or the K8s-deployed
              instance per <code>infra/k8s/observability/grafana.yaml</code>) to embed real dashboards here.
            </p>
          ) : grafanaReachable === false ? (
            <p className="text-sm text-muted">
              Grafana at <code>{grafanaUrl}</code> is not reachable from this browser. Open it directly:{" "}
              <a href={grafanaUrl} target="_blank" rel="noreferrer" className="text-brand underline">
                {grafanaUrl}
              </a>
            </p>
          ) : (
            <>
              <a href={grafanaUrl} target="_blank" rel="noreferrer" className="text-sm text-brand underline">
                Open Grafana in a new tab
              </a>
              <iframe
                src={grafanaUrl}
                title="Grafana"
                className="h-[600px] w-full rounded-md border border-border"
              />
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
