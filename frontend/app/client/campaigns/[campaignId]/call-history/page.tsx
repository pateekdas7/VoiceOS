import { SectionPlaceholder } from "@/components/client/section-placeholder";

export default function CampaignCallHistoryPage() {
  return (
    <SectionPlaceholder
      title="Call History"
      description="Genuinely blocked, not just unwired: no repository/table persists structured call records anywhere in this codebase (see /client/call-history for the full explanation) -- scoping by campaign doesn't change that."
      columns={["Call", "Customer", "Outcome", "Duration", "Started"]}
      note="0 calls"
    />
  );
}
