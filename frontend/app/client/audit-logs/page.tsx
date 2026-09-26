import { SectionPlaceholder } from "@/components/client/section-placeholder";

export default function ClientAuditLogsPage() {
  return (
    <SectionPlaceholder
      title="Audit Logs"
      description="This tenant's own audit trail. AuditRepository.iter_chain() already exists and is tenant-scoped by construction (AR-8) -- what's missing is a tenant-facing BFF route (GET /audit-logs under require_tenant_permission) exposing it, distinct from the admin-only cross-tenant /admin/clients/{id}/audit-logs this pass built. A cheap follow-up, not a new backend capability."
      columns={["Action", "Resource", "Actor", "Outcome", "Recorded"]}
      note="0 events"
    />
  );
}
