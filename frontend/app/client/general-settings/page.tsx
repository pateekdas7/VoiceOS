import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function FormField({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium">{label}</label>
      {hint ? <p className="text-xs text-muted">{hint}</p> : null}
      {children}
    </div>
  );
}

function DisabledInput({ value, placeholder }: { value?: string; placeholder?: string }) {
  return (
    <input
      disabled
      defaultValue={value}
      placeholder={placeholder}
      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm opacity-60"
    />
  );
}

function DisabledSelect({ value, options }: { value: string; options: string[] }) {
  return (
    <select
      disabled
      defaultValue={value}
      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm opacity-60"
    >
      {options.map((o) => <option key={o}>{o}</option>)}
    </select>
  );
}

export default function GeneralSettingsPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold">General Settings</h1>
        <p className="mt-1 text-xs text-muted">
          Workspace profile settings. Fields are read from your tenant record — save requires the update tenant BFF route.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Workspace Profile</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          <FormField label="Display Name" hint="The name shown across dashboards and reports.">
            <DisabledInput value="Acme Corp" />
          </FormField>
          <FormField label="Slug" hint="URL-safe identifier for this workspace. Cannot be changed after creation.">
            <DisabledInput value="acme-corp" />
          </FormField>
          <div className="grid grid-cols-2 gap-4">
            <FormField label="Timezone">
              <DisabledSelect
                value="Asia/Kolkata"
                options={["Asia/Kolkata", "Asia/Dubai", "UTC", "America/New_York", "Europe/London"]}
              />
            </FormField>
            <FormField label="Currency">
              <DisabledSelect value="INR" options={["INR", "USD", "AED", "EUR", "GBP"]} />
            </FormField>
          </div>
          <FormField label="Max Concurrent Calls" hint="Hard cap set by platform admin. Contact support to increase.">
            <DisabledInput value="10" />
          </FormField>
          <FormField label="Subscription Tier">
            <DisabledInput value="ENTERPRISE" />
          </FormField>
          <div className="flex justify-end pt-2">
            <button
              disabled
              className="cursor-not-allowed rounded-md bg-brand px-4 py-2 text-sm font-medium text-brand-foreground opacity-40"
              title="Save requires PUT /tenants/{id} BFF route"
            >
              Save Changes
            </button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Calling Window</CardTitle>
          <p className="mt-1 text-xs text-muted">Default daily window applied to all campaigns in this workspace unless overridden per-campaign.</p>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          <div className="grid grid-cols-2 gap-4">
            <FormField label="Start Hour (24h)">
              <DisabledInput value="9" />
            </FormField>
            <FormField label="End Hour (24h)">
              <DisabledInput value="18" />
            </FormField>
          </div>
          <div className="flex justify-end">
            <button disabled className="cursor-not-allowed rounded-md bg-brand px-4 py-2 text-sm font-medium text-brand-foreground opacity-40">
              Save
            </button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
