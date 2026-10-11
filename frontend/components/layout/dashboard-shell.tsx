import type { NavItem } from "@/lib/nav";
import { NavLinks } from "@/components/layout/nav-links";
import { HealthStrip } from "@/components/layout/health-strip";

export function DashboardShell({
  productLabel,
  actorLabel,
  navItems,
  basePath,
  healthComponents,
  navSlot,
  children,
}: {
  productLabel: string;
  actorLabel: string;
  navItems: NavItem[];
  basePath: string;
  healthComponents: string[];
  /** Overrides the default flat NavLinks render (e.g. ClientNavTree's expandable Campaigns tree). */
  navSlot?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen">
      <aside className="flex w-60 shrink-0 flex-col overflow-y-auto border-r border-sidebar-border bg-sidebar-bg py-4">
        <div className="px-4 pb-4">
          <p className="text-sm font-semibold text-sidebar-foreground-active">VoiceOS</p>
          <p className="text-xs text-sidebar-foreground">{productLabel}</p>
        </div>
        {navSlot ?? <NavLinks items={navItems} basePath={basePath} />}
        <div className="mt-auto px-4 pt-4 text-xs text-sidebar-foreground">{actorLabel}</div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-border bg-surface px-6 py-3">
          <HealthStrip components={healthComponents} />
        </header>
        <main className="flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  );
}
