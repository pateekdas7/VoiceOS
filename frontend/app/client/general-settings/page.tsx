import { SectionPlaceholder } from "@/components/client/section-placeholder";

export default function GeneralSettingsPage() {
  return (
    <SectionPlaceholder
      title="General Settings"
      description="Tenant profile (display name, timezone, currency) -- TenantService already stores these fields; only an edit UI/route is missing. Voice Profiles, notification preferences, and API rate limits live at /client/settings (also a structured placeholder -- VoiceProfile and the notifications service are net-new backend entities, same category as Pipeline)."
      columns={[]}
      note="Tenant profile form"
    />
  );
}
