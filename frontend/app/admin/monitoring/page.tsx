import { redirect } from "next/navigation";

// The single "Monitoring" nav entry split into System Health / Alerts Center /
// Incident Timeline / AI Insights / AI Reports / Capacity Planning -- this bare
// route now just lands on the first of those.
export default function AdminMonitoringPage() {
  redirect("/admin/monitoring/system-health");
}
