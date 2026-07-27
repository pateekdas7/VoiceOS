import { SectionPlaceholder } from "@/components/client/section-placeholder";

export default function IntegrationsPage() {
  return (
    <SectionPlaceholder
      title="Integrations"
      description="Outbound webhooks and third-party connections (ADR-005 §6.13). No backend module exists for this yet."
      columns={["Integration", "Status", "Last Synced"]}
      primaryAction="Add Integration"
      note="0 integrations"
    />
  );
}
