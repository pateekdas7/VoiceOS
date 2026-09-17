import type { HealthStatus } from "@/lib/types";

export function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "healthy" | "warning" | "critical" | "brand";
}) {
  const toneClasses: Record<string, string> = {
    neutral: "bg-status-neutral-bg text-status-neutral",
    healthy: "bg-status-healthy-bg text-status-healthy",
    warning: "bg-status-warning-bg text-status-warning",
    critical: "bg-status-critical-bg text-status-critical",
    brand: "bg-brand text-brand-foreground",
  };

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${toneClasses[tone]}`}
    >
      {children}
    </span>
  );
}

const HEALTH_LABEL: Record<HealthStatus, string> = {
  healthy: "Healthy",
  warning: "Warning",
  critical: "Critical",
  unknown: "Unknown",
};

const HEALTH_TONE: Record<HealthStatus, "healthy" | "warning" | "critical" | "neutral"> = {
  healthy: "healthy",
  warning: "warning",
  critical: "critical",
  unknown: "neutral",
};

// Shared health indicator per ADR-005 §9 — every dashboard reads from the same
// GET /system/health contract, so this is the one component every page uses.
export function HealthBadge({
  component,
  status,
}: {
  component: string;
  status: HealthStatus;
}) {
  return (
    <span className="inline-flex items-center gap-2 text-xs">
      <span
        className={`h-2 w-2 rounded-full ${
          status === "healthy"
            ? "bg-status-healthy"
            : status === "warning"
              ? "bg-status-warning"
              : status === "critical"
                ? "bg-status-critical"
                : "bg-status-neutral"
        }`}
        aria-hidden
      />
      <span className="text-muted">{component}</span>
      <Badge tone={HEALTH_TONE[status]}>{HEALTH_LABEL[status]}</Badge>
    </span>
  );
}
