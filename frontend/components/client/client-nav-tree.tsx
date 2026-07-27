"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { listCampaigns, type Campaign } from "@/lib/api/campaigns";
import { CLIENT_TOP_NAV } from "@/lib/nav";

// Expandable Campaign -> Pipeline product-workflow sidebar. Only "Campaigns"
// expands against real data (listCampaigns() is already a working BFF route);
// per-campaign sub-sections (Pipelines/Leads/CRM/Settings/Analytics/Call
// History) are plain links into that campaign's own layout, which renders
// the deeper Pipeline-level tabs contextually (see
// app/client/campaigns/[campaignId]/layout.tsx) rather than as further
// sidebar nesting -- Pipeline has no backend yet to enumerate, so nesting
// fabricated pipeline rows into the tree would misrepresent real data as
// present.
//
// RBAC-aware nav: session role/permissions are carried in an httpOnly cookie
// (voiceos_session) -- by design, unreadable from client JS (that's what
// httpOnly means; auth_middleware.py never intended a client-side read path).
// There is no GET /auth/me-style endpoint yet exposing "my own permissions"
// to the frontend, so `permission` gating on CLIENT_TOP_NAV is defined but
// currently a no-op (every item renders for every signed-in tenant user) --
// wiring it for real requires that new endpoint, explicitly out of scope for
// this frontend-structure-only pass ("do not wire backend APIs").
const CAMPAIGN_SUB_NAV = [
  { label: "Pipelines", segment: "pipelines" },
  { label: "Leads", segment: "leads" },
  { label: "Campaign CRM", segment: "crm" },
  { label: "Campaign Settings", segment: "settings" },
  { label: "Analytics", segment: "analytics" },
  { label: "Call History", segment: "call-history" },
];

export function ClientNavTree({ basePath }: { basePath: string }) {
  const pathname = usePathname();
  const [campaignsExpanded, setCampaignsExpanded] = useState(true);
  const [campaigns, setCampaigns] = useState<Campaign[] | null>(null);
  // User-driven collapse/expand override, keyed by campaignId -- absent
  // entries fall back to whether the current route is inside that campaign
  // (derived straight from `pathname` during render, not synced via effect).
  const [manualExpand, setManualExpand] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    listCampaigns()
      .then((data) => {
        if (!cancelled) setCampaigns(data);
      })
      .catch(() => {
        if (!cancelled) setCampaigns([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const pathnameCampaignId = pathname.match(/\/campaigns\/([^/]+)/)?.[1] ?? null;

  return (
    <nav className="flex flex-col gap-0.5 px-3">
      {CLIENT_TOP_NAV.map((item) => {
        if (item.label !== "Campaigns") {
          const href = `${basePath}${item.href}`;
          const isActive = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              className={`rounded-md px-3 py-2 text-sm transition-colors ${
                isActive
                  ? "bg-white/10 font-medium text-sidebar-foreground-active"
                  : "text-sidebar-foreground hover:bg-white/5 hover:text-sidebar-foreground-active"
              }`}
            >
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
                className="px-2 py-2 text-sidebar-foreground hover:text-sidebar-foreground-active"
                aria-label={campaignsExpanded ? "Collapse Campaigns" : "Expand Campaigns"}
              >
                {campaignsExpanded ? "▾" : "▸"}
              </button>
              <Link
                href={listHref}
                className={`flex-1 rounded-md px-1 py-2 text-sm transition-colors ${
                  isListActive
                    ? "bg-white/10 font-medium text-sidebar-foreground-active"
                    : "text-sidebar-foreground hover:bg-white/5 hover:text-sidebar-foreground-active"
                }`}
              >
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
                            className="px-1.5 py-1.5 text-xs text-sidebar-foreground hover:text-sidebar-foreground-active"
                            aria-label={isExpanded ? `Collapse ${c.name}` : `Expand ${c.name}`}
                          >
                            {isExpanded ? "▾" : "▸"}
                          </button>
                          <Link
                            href={campaignHref}
                            className={`flex-1 truncate rounded-md px-1 py-1.5 text-xs transition-colors ${
                              pathname === campaignHref
                                ? "bg-white/10 font-medium text-sidebar-foreground-active"
                                : "text-sidebar-foreground hover:bg-white/5 hover:text-sidebar-foreground-active"
                            }`}
                            title={c.name}
                          >
                            {c.name}
                          </Link>
                        </div>
                        {isExpanded ? (
                          <div className="ml-4 flex flex-col gap-0.5 border-l border-sidebar-border pl-2">
                            {CAMPAIGN_SUB_NAV.map((sub) => {
                              const subHref = `${campaignHref}/${sub.segment}`;
                              const isSubActive = pathname === subHref || pathname.startsWith(`${subHref}/`);
                              return (
                                <Link
                                  key={sub.segment}
                                  href={subHref}
                                  className={`rounded-md px-2 py-1 text-xs transition-colors ${
                                    isSubActive
                                      ? "bg-white/10 font-medium text-sidebar-foreground-active"
                                      : "text-sidebar-foreground hover:bg-white/5 hover:text-sidebar-foreground-active"
                                  }`}
                                >
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
