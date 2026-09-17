"use client";

import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { StatTile } from "@/components/ui/card";
import { ApiError, getDashboardSnapshot, type DashboardSnapshot } from "@/lib/api/client-ops";

export function AnalyticsView() {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDashboardSnapshot()
      .then((data) => {
        if (cancelled) return;
        setSnapshot(data);
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
  if (snapshot === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading analytics…</CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">Analytics</h1>
      <p className="text-xs text-muted">Last 24 hours, as of {new Date(snapshot.as_of).toLocaleString()}.</p>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatTile label="Calls Completed" value={String(snapshot.calls_completed)} />
        <StatTile label="Avg Duration" value={`${(snapshot.average_duration_ms / 1000).toFixed(1)}s`} />
        <StatTile label="Contactability" value={`${(snapshot.contactability_rate * 100).toFixed(1)}%`} />
        <StatTile label="Recovery Rate" value={`${(snapshot.recovery_rate * 100).toFixed(1)}%`} />
      </div>
      <Card>
        <CardContent>
          <p className="mb-2 text-xs font-semibold text-muted">Outcome Distribution</p>
          {Object.keys(snapshot.outcome_distribution).length === 0 ? (
            <p className="text-sm text-muted">No call outcomes recorded yet in this window.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {Object.entries(snapshot.outcome_distribution).map(([outcome, count]) => (
                <li key={outcome} className="text-sm text-muted">
                  {outcome}: {count}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
