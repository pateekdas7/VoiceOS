import { EmptyState } from "@/components/ui/empty-state";

export default function ClientCallHistoryPage() {
  return (
    <EmptyState
      title="Call History"
      description="Genuinely blocked, not just unwired: no repository/table persists structured call records anywhere in this codebase. media_gateway.call_recorder.CallRecorder writes only local per-call JSONL/WAV files (Sprint-029 acceptance-review tooling), never a queryable calls table -- there is nothing yet to build a list view on top of, independent of whether live GPU/telephony calls exist in this environment."
      adrRef="ADR-005 §6.6 / §10"
      status="backend-pending"
    />
  );
}
