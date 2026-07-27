import { SectionPlaceholder } from "@/components/client/section-placeholder";

export default function CampaignSettingsPage() {
  return (
    <SectionPlaceholder
      title="Campaign Settings"
      description="Retry policy, audience criteria, daily calling window, and default strategy -- CampaignService already stores all of these fields; only an edit UI/route is missing (create-time only today)."
      columns={[]}
      note="Campaign settings form"
    />
  );
}
