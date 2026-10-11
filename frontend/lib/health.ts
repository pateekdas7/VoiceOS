import type { HealthStatus } from "@/lib/types";

// The BFF's GET /system/health emits src.libs.health.protocol.HealthStatus's
// raw values -- "healthy" | "degraded" | "unhealthy" (see health_checks.py) --
// not this frontend's own "healthy" | "warning" | "critical" | "unknown"
// vocabulary. Every consumer of /system/health must go through this mapping;
// comparing a raw backend value directly against "warning"/"critical" (as an
// earlier version of HealthStrip did) silently never matches, showing a truly
// degraded/unhealthy backend as a neutral grey dot instead of red.
export function toHealthStatus(rawStatus: string): HealthStatus {
  switch (rawStatus) {
    case "healthy":
      return "healthy";
    case "degraded":
      return "warning";
    case "unhealthy":
      return "critical";
    default:
      return "unknown";
  }
}
