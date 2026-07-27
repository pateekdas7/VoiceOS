"use client";

import { use, useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { StatTile } from "@/components/ui/card";
import { ApiError, getCampaignAnalyticsSummary } from "@/lib/api/client-ops";

export default function CampaignAnalyticsPage({ params }: { params: Promise<{ campaignId: string }> }) {
  const { campaignId } = use(params);
  const [summary, setSummary] = useState<{ ptp_rate: number; contactability_rate: number; conversion_rate: number } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getCampaignAnalyticsSummary(campaignId)
      .then((data) => {
        if (!cancelled) setSummary(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId]);

  if (error) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
      </Card>
    );
  }
  if (summary === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading analytics…</CardContent>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-3 gap-4">
      <StatTile label="PTP Rate" value={`${(summary.ptp_rate * 100).toFixed(1)}%`} />
      <StatTile label="Contactability" value={`${(summary.contactability_rate * 100).toFixed(1)}%`} />
      <StatTile label="Conversion Rate" value={`${(summary.conversion_rate * 100).toFixed(1)}%`} />
    </div>
  );
}
