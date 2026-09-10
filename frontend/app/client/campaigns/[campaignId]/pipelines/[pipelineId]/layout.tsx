"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Breadcrumbs } from "@/components/client/breadcrumbs";
import { getPipeline } from "@/lib/local-pipelines";

const TABS = [
  { label: "Leads", segment: "leads" },
  { label: "Pipeline Settings", segment: "settings" },
  { label: "Pipeline CRM", segment: "crm" },
  { label: "Pipeline Analytics", segment: "analytics" },
  { label: "Execution History", segment: "execution-history" },
];

export default function PipelineLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ campaignId: string; pipelineId: string }>;
}) {
  const pathname = usePathname();
  const [ids, setIds] = useState<{ campaignId: string; pipelineId: string } | null>(null);
  const [pipelineName, setPipelineName] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    params.then((resolved) => {
      if (!cancelled) setIds(resolved);
    });
    return () => {
      cancelled = true;
    };
  }, [params]);

  useEffect(() => {
    if (!ids) return;
    let cancelled = false;
    getPipeline(ids.campaignId, ids.pipelineId).then((pipeline) => {
      if (!cancelled) setPipelineName(pipeline?.name ?? null);
    });
    return () => {
      cancelled = true;
    };
  }, [ids]);

  const base = ids ? `/client/campaigns/${ids.campaignId}/pipelines/${ids.pipelineId}` : "";

  return (
    <div className="flex flex-col gap-4">
      <Breadcrumbs
        items={[
          { label: "Campaigns", href: "/client/campaigns" },
          { label: "Pipelines", href: ids ? `/client/campaigns/${ids.campaignId}/pipelines` : undefined },
          { label: pipelineName ?? ids?.pipelineId ?? "…" },
        ]}
      />
      <div className="flex gap-1 border-b border-border">
        {TABS.map((tab) => {
          const href = `${base}/${tab.segment}`;
          const isActive = pathname === href;
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
