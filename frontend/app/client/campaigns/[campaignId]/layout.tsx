"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Breadcrumbs } from "@/components/client/breadcrumbs";
import { ApiError, getCampaign, type Campaign } from "@/lib/api/campaigns";

const TABS = [
  { label: "Pipelines", segment: "pipelines" },
  { label: "Leads", segment: "leads" },
  { label: "Campaign CRM", segment: "crm" },
  { label: "Campaign Settings", segment: "settings" },
  { label: "Analytics", segment: "analytics" },
  { label: "Call History", segment: "call-history" },
];

export default function CampaignLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ campaignId: string }>;
}) {
  const pathname = usePathname();
  const [campaignId, setCampaignId] = useState<string | null>(null);
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    params.then(({ campaignId }) => {
      if (!cancelled) setCampaignId(campaignId);
    });
    return () => {
      cancelled = true;
    };
  }, [params]);

  useEffect(() => {
    if (!campaignId) return;
    let cancelled = false;
    getCampaign(campaignId)
      .then((data) => {
        if (!cancelled) setCampaign(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId]);

  const base = campaignId ? `/client/campaigns/${campaignId}` : "";

  return (
    <div className="flex flex-col gap-4">
      <Breadcrumbs
        items={[
          { label: "Campaigns", href: "/client/campaigns" },
          { label: campaign?.name ?? campaignId ?? "…" },
        ]}
      />
      {error ? <p className="text-sm text-status-critical">{error}</p> : null}
      <div className="flex gap-1 border-b border-border">
        {TABS.map((tab) => {
          const href = `${base}/${tab.segment}`;
          const isActive = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={tab.segment}
              href={href}
              className={`border-b-2 px-3 py-2 text-sm transition-colors ${
                isActive
                  ? "border-brand font-medium text-foreground"
                  : "border-transparent text-muted hover:text-foreground"
              }`}
            >
              {tab.label}
            </Link>
          );
        })}
      </div>
      {children}
    </div>
  );
}
