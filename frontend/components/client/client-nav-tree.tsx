"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { listCampaigns, type Campaign } from "@/lib/api/campaigns";
import { listPipelines, type LocalPipeline } from "@/lib/local-pipelines";
import { CLIENT_TOP_NAV } from "@/lib/nav";

const CAMPAIGN_SUB_NAV = [
  { label: "Pipelines", segment: "pipelines" },
  { label: "Leads", segment: "leads" },
  { label: "Campaign CRM", segment: "crm" },
  { label: "Campaign Settings", segment: "settings" },
  { label: "Analytics", segment: "analytics" },
  { label: "Call History", segment: "call-history" },
];

const PIPELINE_SUB_NAV = [
  { label: "Leads", segment: "leads" },
  { label: "Pipeline Settings", segment: "settings" },
  { label: "Pipeline CRM", segment: "crm" },
  { label: "Pipeline Analytics", segment: "analytics" },
  { label: "Execution History", segment: "execution-history" },
];

function navLink(active: boolean) {
  return `rounded-md px-3 py-2 text-sm transition-colors ${
    active
      ? "bg-white/10 font-medium text-sidebar-foreground-active"
      : "text-sidebar-foreground hover:bg-white/5 hover:text-sidebar-foreground-active"
  }`;
}
function subLink(active: boolean) {
  return `rounded-md px-2 py-1 text-xs transition-colors ${
    active
      ? "bg-white/10 font-medium text-sidebar-foreground-active"
      : "text-sidebar-foreground hover:bg-white/5 hover:text-sidebar-foreground-active"
  }`;
}
function deepLink(active: boolean) {
  return `rounded-md px-2 py-0.5 text-[11px] transition-colors ${
    active
      ? "bg-white/10 font-medium text-sidebar-foreground-active"
      : "text-sidebar-foreground hover:bg-white/5 hover:text-sidebar-foreground-active"
  }`;
}
function chevronBtn(size: "sm" | "xs") {
  return size === "sm"
    ? "px-2 py-2 text-sidebar-foreground hover:text-sidebar-foreground-active"
    : "px-1.5 py-1.5 text-xs text-sidebar-foreground hover:text-sidebar-foreground-active";
}

export function ClientNavTree({ basePath }: { basePath: string }) {
  const pathname = usePathname();
  const [campaignsExpanded, setCampaignsExpanded] = useState(true);
  const [campaigns, setCampaigns] = useState<Campaign[] | null>(null);
  const [manualExpand, setManualExpand] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    listCampaigns()
      .then((data) => { if (!cancelled) setCampaigns(data); })
      .catch(() => { if (!cancelled) setCampaigns([]); });
    return () => { cancelled = true; };
  }, [pathname]);

  const pathnameCampaignId = pathname.match(/\/campaigns\/([^/]+)/)?.[1] ?? null;

  return (
    <nav className="flex flex-col gap-0.5 px-3">
      {CLIENT_TOP_NAV.map((item) => {
        if (item.label !== "Campaigns") {
          const href = `${basePath}${item.href}`;
          const isActive = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link key={href} href={href} className={navLink(isActive)}>
              {item.label}
            </Link>
          );
        }

        const listHref = `${basePath}${item.href}`;
        const isListActive = pathname === listHref;

        return (
          <div key={item.href} className="flex flex-col gap-0.5">
            <div className="flex items-center">
              <button
                type="button"
                onClick={() => setCampaignsExpanded((v) => !v)}
                className={chevronBtn("sm")}
                aria-label={campaignsExpanded ? "Collapse Campaigns" : "Expand Campaigns"}
              >
                {campaignsExpanded ? "▾" : "▸"}
              </button>
              <Link href={listHref} className={`flex-1 rounded-md px-1 py-2 text-sm transition-colors ${
                isListActive
                  ? "bg-white/10 font-medium text-sidebar-foreground-active"
                  : "text-sidebar-foreground hover:bg-white/5 hover:text-sidebar-foreground-active"
              }`}>
                Campaigns
              </Link>
            </div>

            {campaignsExpanded ? (
              <div className="ml-4 flex flex-col gap-0.5 border-l border-sidebar-border pl-2">
                {campaigns === null ? (
                  <p className="px-2 py-1 text-xs text-sidebar-foreground">Loading…</p>
                ) : campaigns.length === 0 ? (
                  <p className="px-2 py-1 text-xs text-sidebar-foreground">No campaigns yet.</p>
                ) : (
                  campaigns.map((c) => {
                    const campaignHref = `${basePath}/campaigns/${c.campaign_id}`;
                    const isExpanded = manualExpand[c.campaign_id] ?? pathnameCampaignId === c.campaign_id;
                    return (
                      <div key={c.campaign_id} className="flex flex-col gap-0.5">
                        <div className="flex items-center">
                          <button
                            type="button"
                            onClick={() => setManualExpand((prev) => ({ ...prev, [c.campaign_id]: !isExpanded }))}
                            className={chevronBtn("xs")}
                            aria-label={isExpanded ? `Collapse ${c.name}` : `Expand ${c.name}`}
                          >
                            {isExpanded ? "▾" : "▸"}
                          </button>
                          <Link
                            href={campaignHref}
                            title={c.name}
                            className={`flex-1 truncate rounded-md px-1 py-1.5 text-xs transition-colors ${
                              pathname === campaignHref
                                ? "bg-white/10 font-medium text-sidebar-foreground-active"
                                : "text-sidebar-foreground hover:bg-white/5 hover:text-sidebar-foreground-active"
                            }`}
                          >
                            {c.name}
                          </Link>
                        </div>
                        {isExpanded ? (
                          <div className="ml-4 flex flex-col gap-0.5 border-l border-sidebar-border pl-2">
                            {CAMPAIGN_SUB_NAV.map((sub) =>
                              sub.segment === "pipelines" ? (
                                <PipelinesSubNav
                                  key={sub.segment}
                                  campaignId={c.campaign_id}
                                  campaignHref={campaignHref}
                                  pathname={pathname}
                                />
                              ) : (
                                (() => {
                                  const subHref = `${campaignHref}/${sub.segment}`;
                                  const isSubActive = pathname === subHref || pathname.startsWith(`${subHref}/`);
                                  return (
                                    <Link key={sub.segment} href={subHref} className={subLink(isSubActive)}>
                                      {sub.label}
                                    </Link>
                                  );
                                })()
                              ),
                            )}
                          </div>
                        ) : null}
                      </div>
                    );
                  })
                )}
                <Link
                  href={`${basePath}/campaigns/new`}
                  className="rounded-md px-2 py-1.5 text-xs font-medium text-brand hover:underline"
                >
                  + Create Campaign
                </Link>
              </div>
            ) : null}
          </div>
        );
      })}
    </nav>
  );
}

function PipelinesSubNav({
  campaignId,
  campaignHref,
  pathname,
}: {
  campaignId: string;
  campaignHref: string;
  pathname: string;
}) {
  const listHref = `${campaignHref}/pipelines`;
  const isListActive = pathname === listHref;
  const pathnamePipelineId = pathname.startsWith(`${listHref}/`)
    ? pathname.slice(listHref.length + 1).split("/")[0]
    : null;
  const [expanded, setExpanded] = useState(Boolean(pathnamePipelineId));
  const [pipelines, setPipelines] = useState<LocalPipeline[] | null>(null);
  const [pipelineExpand, setPipelineExpand] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    listPipelines(campaignId).then((data) => {
      if (!cancelled) setPipelines(data);
    });
    return () => { cancelled = true; };
  }, [campaignId, pathname]);

  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-center">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="px-1 py-1 text-xs text-sidebar-foreground hover:text-sidebar-foreground-active"
          aria-label={expanded ? "Collapse Pipelines" : "Expand Pipelines"}
        >
          {expanded ? "▾" : "▸"}
        </button>
        <Link href={listHref} className={`flex-1 rounded-md px-1 py-1 text-xs transition-colors ${
          isListActive
            ? "bg-white/10 font-medium text-sidebar-foreground-active"
            : "text-sidebar-foreground hover:bg-white/5 hover:text-sidebar-foreground-active"
        }`}>
          Pipelines
        </Link>
      </div>
      {expanded ? (
        <div className="ml-4 flex flex-col gap-0.5 border-l border-sidebar-border pl-2">
          {pipelines === null ? (
            <p className="px-2 py-0.5 text-[11px] text-sidebar-foreground">Loading…</p>
          ) : pipelines.length === 0 ? (
            <p className="px-2 py-0.5 text-[11px] text-sidebar-foreground">No pipelines yet.</p>
          ) : (
            pipelines.map((p) => {
              const pipelineHref = `${listHref}/${p.pipeline_id}`;
              const isInsidePipeline = pathname === pipelineHref || pathname.startsWith(`${pipelineHref}/`);
              const isPipelineExpanded = pipelineExpand[p.pipeline_id] ?? pathnamePipelineId === p.pipeline_id;
              return (
                <div key={p.pipeline_id} className="flex flex-col gap-0.5">
                  <div className="flex items-center">
                    <button
                      type="button"
                      onClick={() => setPipelineExpand((prev) => ({ ...prev, [p.pipeline_id]: !isPipelineExpanded }))}
                      className="px-1 py-0.5 text-[11px] text-sidebar-foreground hover:text-sidebar-foreground-active"
                      aria-label={isPipelineExpanded ? `Collapse ${p.name}` : `Expand ${p.name}`}
                    >
                      {isPipelineExpanded ? "▾" : "▸"}
                    </button>
                    <Link
                      href={pipelineHref}
                      title={p.name}
                      className={`flex-1 truncate rounded-md px-1 py-0.5 text-[11px] transition-colors ${
                        isInsidePipeline && !isPipelineExpanded
                          ? "bg-white/10 font-medium text-sidebar-foreground-active"
                          : pathname === pipelineHref
                            ? "bg-white/10 font-medium text-sidebar-foreground-active"
                            : "text-sidebar-foreground hover:bg-white/5 hover:text-sidebar-foreground-active"
                      }`}
                    >
                      {p.name}
                    </Link>
                  </div>
                  {isPipelineExpanded ? (
                    <div className="ml-4 flex flex-col gap-0.5 border-l border-sidebar-border pl-2">
                      {PIPELINE_SUB_NAV.map((sub) => {
                        const subHref = `${pipelineHref}/${sub.segment}`;
                        const isSubActive = pathname === subHref || pathname.startsWith(`${subHref}/`);
                        return (
                          <Link key={sub.segment} href={subHref} className={deepLink(isSubActive)}>
                            {sub.label}
                          </Link>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              );
            })
          )}
          <Link
            href={`${listHref}/new`}
            className="rounded-md px-2 py-0.5 text-[11px] font-medium text-brand hover:underline"
          >
            + Create Pipeline
          </Link>
        </div>
      ) : null}
    </div>
  );
}
