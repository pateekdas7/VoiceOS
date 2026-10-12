import { DashboardShell } from "@/components/layout/dashboard-shell";
import { ADMIN_NAV } from "@/lib/nav";

const ADMIN_HEALTH_COMPONENTS = [
  "PostgreSQL",
  "Redis",
  "GPU Scheduler",
  "STT",
  "LLM",
  "TTS",
  "Event Bus",
  "Workers",
  "Twilio/SIP",
  "CPU Nodes",
  "GPU Nodes",
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <DashboardShell
      productLabel="Admin"
      actorLabel="Platform Owner"
      navItems={ADMIN_NAV}
      basePath="/admin"
      healthComponents={ADMIN_HEALTH_COMPONENTS}
    >
      {children}
    </DashboardShell>
  );
}
