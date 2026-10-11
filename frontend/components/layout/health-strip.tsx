"use client";

import { useEffect, useState } from "react";
import { toHealthStatus } from "@/lib/health";
import type { HealthComponent } from "@/lib/types";

// Reads the single shared GET /system/health contract (ADR-005 §9).
// Until the Web BFF exists, this honestly reports "not connected" rather
// than fabricating a healthy/critical status for infrastructure it cannot see.
export function HealthStrip({ components }: { components: string[] }) {
  const bffUrl = process.env.NEXT_PUBLIC_BFF_URL;
  const [data, setData] = useState<HealthComponent[] | null>(null);
  const [fetchFailed, setFetchFailed] = useState(false);

  useEffect(() => {
    if (!bffUrl) return;

    const controller = new AbortController();
    fetch(`${bffUrl}/system/health`, { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`status ${res.status}`);
        return res.json();
      })
      .then((json: HealthComponent[]) => setData(json))
      .catch(() => setFetchFailed(true));

    return () => controller.abort();
  }, [bffUrl]);

  const connected = Boolean(bffUrl) && !fetchFailed && data !== null;

  if (!connected) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted">
        <span className="h-2 w-2 rounded-full bg-status-neutral" aria-hidden />
        Backend not connected — health data unavailable
      </div>
    );
  }

  const byComponent = new Map((data ?? []).map((c) => [c.component, c]));

  return (
    <div className="flex flex-wrap items-center gap-4">
      {components.map((name) => {
        const entry = byComponent.get(name);
        const status = entry ? toHealthStatus(entry.status) : "unknown";
        return (
          <span key={name} className="flex items-center gap-1.5 text-xs">
            <span
              className={`h-2 w-2 rounded-full ${
                status === "healthy"
                  ? "bg-status-healthy"
                  : status === "warning"
                    ? "bg-status-warning"
                    : status === "critical"
                      ? "bg-status-critical"
                      : "bg-status-neutral"
              }`}
              aria-hidden
            />
            <span className="text-muted">{name}</span>
          </span>
        );
      })}
    </div>
  );
}
