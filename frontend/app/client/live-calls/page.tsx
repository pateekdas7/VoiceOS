import { EscalationsView } from "@/components/client/escalations-view";
import { EmptyState } from "@/components/ui/empty-state";

export default function ClientLiveCallsPage() {
  return (
    <div className="flex flex-col gap-6">
      <EmptyState
        title="Active Call Monitor"
        description="Real-time view of in-progress calls for this tenant. Requires a live GPU/telephony pipeline (Volume 1 runtime) streaming call state into this BFF — no such stream exists in this environment, so this section stays honest rather than faking data."
        adrRef="ADR-005 §6.6"
        status="backend-pending"
      />
      <EscalationsView />
    </div>
  );
}
