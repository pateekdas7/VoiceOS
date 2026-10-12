"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, getIncidentTimeline, type AlertRecord, type Insight, type TimelineEntry } from "@/lib/api/admin-ops";

export function IncidentTimelineView() {
  const [entries, setEntries] = useState<TimelineEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getIncidentTimeline()
      .then((data) => {
        if (cancelled) return;
        setEntries(data);
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
  if (entries === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading incident timeline…</CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">Incident Timeline</h1>
      <p className="text-xs text-muted">
        A derived chronological merge of open alerts and generated AI insights -- there is no dedicated
        &quot;Incident&quot; entity in ADR-006. Currently shows only OPEN alerts (no list-all-including-resolved
        query exists yet); AI Insights are shown regardless of the reasoning flag&apos;s current state since they
        were already persisted.
      </p>
      {entries.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted">Nothing on the timeline yet.</CardContent>
        </Card>
      ) : (
        <div className="flex flex-col gap-3">
          {entries.map((entry, i) => (
            <Card key={i}>
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  <Badge tone={entry.type === "alert" ? "warning" : "brand"}>{entry.type}</Badge>
                  <span className="text-xs text-muted">{new Date(entry.timestamp).toLocaleString()}</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="text-sm">
                {entry.type === "alert" ? (
                  <p>
                    <span className="font-medium">{(entry.event as AlertRecord).source}</span> --{" "}
                    {(entry.event as AlertRecord).severity} -- {(entry.event as AlertRecord).status}
                  </p>
                ) : (
                  <p>
                    <span className="font-medium">{(entry.event as Insight).category}</span> --{" "}
                    {(entry.event as Insight).recommendation ?? "no recommendation"}
                  </p>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
