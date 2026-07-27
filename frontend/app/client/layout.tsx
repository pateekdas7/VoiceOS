import { ClientNavTree } from "@/components/client/client-nav-tree";
import { DashboardShell } from "@/components/layout/dashboard-shell";
import { CLIENT_NAV } from "@/lib/nav";

// Reduced set per ADR-005 §9: a tenant client never sees raw node-level
// GPU/CPU health — that stays Admin-only (§5.2 tenant-isolation boundary).
const CLIENT_HEALTH_COMPONENTS = ["STT", "LLM", "TTS", "Twilio/SIP", "Event Bus"];

export default function ClientLayout({ children }: { children: React.ReactNode }) {
  return (
    <DashboardShell
      productLabel="Client"
      actorLabel="Tenant Workspace"
      navItems={CLIENT_NAV}
      basePath="/client"
      healthComponents={CLIENT_HEALTH_COMPONENTS}
      navSlot={<ClientNavTree basePath="/client" />}
    >
      {children}
    </DashboardShell>
  );
}
