import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function SettingsSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description ? <p className="mt-1 text-xs text-muted">{description}</p> : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">{children}</CardContent>
    </Card>
  );
}

function FieldRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border pb-4 last:border-0 last:pb-0">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{label}</p>
        {hint ? <p className="mt-0.5 text-xs text-muted">{hint}</p> : null}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function Toggle({ disabled = true }: { disabled?: boolean }) {
  return (
    <button
      type="button"
      disabled={disabled}
      className="relative inline-flex h-5 w-9 cursor-not-allowed items-center rounded-full bg-gray-300 opacity-50"
      title="Not yet connected to backend"
    >
      <span className="inline-block h-4 w-4 translate-x-0.5 rounded-full bg-white shadow" />
    </button>
  );
}

function DisabledInput({ value, placeholder }: { value?: string; placeholder?: string }) {
  return (
    <input
      disabled
      defaultValue={value}
      placeholder={placeholder}
      className="w-48 rounded-md border border-border bg-background px-2.5 py-1.5 text-sm text-muted opacity-60"
    />
  );
}

export default function ClientSettingsPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold">Settings</h1>
        <p className="mt-1 text-xs text-muted">Manage voice profiles, notifications, API limits, and workspace preferences.</p>
      </div>

      <SettingsSection
        title="Voice Profiles"
        description="TTS speaker personas for this workspace. Each pipeline can select a different voice profile."
      >
        <div className="flex items-center justify-between rounded-lg border border-border bg-background p-4">
          <div>
            <p className="text-sm font-medium text-muted">No voice profiles configured</p>
            <p className="text-xs text-muted">Voice profile management requires the TTS speaker service (backend pending).</p>
          </div>
          <button
            disabled
            className="cursor-not-allowed rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground opacity-40"
          >
            Add Profile
          </button>
        </div>
      </SettingsSection>

      <SettingsSection
        title="Notification Preferences"
        description="Choose which events trigger in-app or email notifications."
      >
        <FieldRow label="Campaign completed" hint="Notify when all leads in a campaign are processed.">
          <Toggle />
        </FieldRow>
        <FieldRow label="Pipeline failure" hint="Alert when a pipeline execution fails or errors.">
          <Toggle />
        </FieldRow>
        <FieldRow label="HITL escalation" hint="Notify when a call is escalated to a human agent.">
          <Toggle />
        </FieldRow>
        <FieldRow label="Weekly summary email" hint="Receive a weekly digest of campaign performance.">
          <Toggle />
        </FieldRow>
      </SettingsSection>

      <SettingsSection
        title="API Rate Limits"
        description="Per-tenant concurrency and throughput limits. Contact your platform admin to change these."
      >
        <FieldRow label="Max concurrent calls" hint="Simultaneous voice calls allowed for this workspace.">
          <DisabledInput value="10" />
        </FieldRow>
        <FieldRow label="Calls per minute" hint="Peak outbound call rate.">
          <DisabledInput value="30" />
        </FieldRow>
        <FieldRow label="API requests per minute" hint="BFF API rate limit for this tenant.">
          <DisabledInput value="300" />
        </FieldRow>
      </SettingsSection>

      <SettingsSection
        title="Danger Zone"
        description="Irreversible workspace actions."
      >
        <FieldRow label="Export all data" hint="Download a full export of campaigns, leads, and call history.">
          <button disabled className="cursor-not-allowed rounded-md border border-border px-3 py-1.5 text-sm opacity-40">
            Request Export
          </button>
        </FieldRow>
        <FieldRow label="Delete workspace" hint="Permanently delete this tenant and all data. This cannot be undone.">
          <button disabled className="cursor-not-allowed rounded-md border border-status-critical px-3 py-1.5 text-sm text-status-critical opacity-40">
            Delete Workspace
          </button>
        </FieldRow>
      </SettingsSection>
    </div>
  );
}
