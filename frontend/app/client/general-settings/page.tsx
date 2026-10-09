"use client";

import { useEffect, useState } from "react";
import { ApiError, bffGet, bffPut } from "@/lib/api/fetch-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type TenantProfile = {
  tenant_id: string;
  slug: string;
  display_name: string;
  subscription_tier: string;
  status: string;
  timezone: string;
  currency: string;
  max_concurrent_calls: number;
  created_at: string;
  updated_at: string;
};

const TIMEZONES = [
  "Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Asia/Bangkok",
  "Asia/Tokyo", "Asia/Karachi", "Asia/Dhaka", "UTC",
  "America/New_York", "America/Chicago", "America/Los_Angeles",
  "Europe/London", "Europe/Paris", "Europe/Berlin",
  "Australia/Sydney", "Pacific/Auckland",
];

const CURRENCIES = ["INR", "USD", "AED", "EUR", "GBP", "SGD", "AUD", "BDT", "PKR"];

function ReadOnlyField({ label, hint, value }: { label: string; hint?: string; value: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium">{label}</label>
      {hint && <p className="text-xs text-muted">{hint}</p>}
      <input
        disabled
        value={value}
        className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm opacity-60"
      />
    </div>
  );
}

export default function GeneralSettingsPage() {
  const [profile, setProfile] = useState<TenantProfile | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Editable form state
  const [displayName, setDisplayName] = useState("");
  const [timezone, setTimezone] = useState("");
  const [currency, setCurrency] = useState("");

  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  useEffect(() => {
    let cancelled = false;
    bffGet<TenantProfile>("/tenants/me")
      .then((data) => {
        if (cancelled) return;
        setProfile(data);
        setDisplayName(data.display_name);
        setTimezone(data.timezone);
        setCurrency(data.currency.trim());
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err instanceof ApiError ? err.message : "Could not load workspace profile.");
      });
    return () => { cancelled = true; };
  }, []);

  function markDirty() {
    setDirty(true);
    setSaveSuccess(false);
    setSaveError(null);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSaveError(null);
    setSaveSuccess(false);
    try {
      const updated = await bffPut<TenantProfile>("/tenants/me", {
        display_name: displayName,
        timezone,
        currency,
      });
      setProfile(updated);
      setDirty(false);
      setSaveSuccess(true);
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  if (loadError) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-lg font-semibold">General Settings</h1>
        <p className="text-sm text-status-critical">{loadError}</p>
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-lg font-semibold">General Settings</h1>
        <p className="text-sm text-muted">Loading\u2026</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold">General Settings</h1>
        <p className="mt-1 text-xs text-muted">
          Workspace profile. Slug, tier, and concurrency limits are managed by your platform admin.
        </p>
      </div>

      <form onSubmit={handleSave} className="flex flex-col gap-6">
        <Card>
          <CardHeader><CardTitle>Workspace Profile</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-5">
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-medium" htmlFor="display_name">Display Name</label>
              <p className="text-xs text-muted">The name shown across dashboards and reports.</p>
              <input
                id="display_name"
                required
                value={displayName}
                onChange={(e) => { setDisplayName(e.target.value); markDirty(); }}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand/50"
              />
            </div>

            <ReadOnlyField
              label="Slug"
              hint="URL-safe identifier. Cannot be changed after creation."
              value={profile.slug}
            />

            <div className="grid grid-cols-2 gap-4">
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium" htmlFor="timezone">Timezone</label>
                <select
                  id="timezone"
                  value={timezone}
                  onChange={(e) => { setTimezone(e.target.value); markDirty(); }}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand/50"
                >
                  {TIMEZONES.map((tz) => <option key={tz} value={tz}>{tz}</option>)}
                </select>
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium" htmlFor="currency">Currency</label>
                <select
                  id="currency"
                  value={currency}
                  onChange={(e) => { setCurrency(e.target.value); markDirty(); }}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand/50"
                >
                  {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
            </div>

            <ReadOnlyField
              label="Max Concurrent Calls"
              hint="Hard cap set by platform admin. Contact support to increase."
              value={String(profile.max_concurrent_calls)}
            />

            <ReadOnlyField
              label="Subscription Tier"
              value={profile.subscription_tier}
            />

            <div className="flex items-center justify-between border-t border-border pt-4">
              <div className="text-xs text-muted">
                {saveSuccess && <span className="text-status-healthy">Changes saved.</span>}
                {saveError && <span className="text-status-critical">{saveError}</span>}
                {!saveSuccess && !saveError && dirty && <span>You have unsaved changes.</span>}
                {!dirty && !saveSuccess && (
                  <span>Last updated {new Date(profile.updated_at).toLocaleString()}</span>
                )}
              </div>
              <button
                type="submit"
                disabled={saving || !dirty}
                className="rounded-md bg-brand px-4 py-2 text-sm font-medium text-brand-foreground disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {saving ? "Saving\u2026" : "Save Changes"}
              </button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Calling Window</CardTitle>
            <p className="mt-1 text-xs text-muted">
              Calling windows are configured per-campaign under Campaign Settings.
              The values here show platform defaults \u2014 contact your admin to change them globally.
            </p>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-4">
            <ReadOnlyField label="Default Start Hour (24h)" value="9" />
            <ReadOnlyField label="Default End Hour (24h)" value="18" />
          </CardContent>
        </Card>
      </form>
    </div>
  );
}
