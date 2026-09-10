"use client";

import { use, useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Campaign = {
  campaign_id: string;
  name: string;
  description: string;
  status: string;
  daily_start_hour: number;
  daily_end_hour: number;
  timezone: string;
  target_call_count: number;
};

const bff = process.env.NEXT_PUBLIC_BFF_URL || "/bff";
const TIMEZONES = ["Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "UTC", "America/New_York", "Europe/London"];

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium">{label}</label>
      {hint && <p className="text-xs text-muted">{hint}</p>}
      {children}
    </div>
  );
}

const inputCls = "w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-brand";
const selectCls = inputCls;

export default function CampaignSettingsPage({
  params,
}: {
  params: Promise<{ campaignId: string }>;
}) {
  const { campaignId } = use(params);
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [form, setForm] = useState<Partial<Campaign>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const r = await fetch(`${bff}/campaigns/${campaignId}`, { credentials: "include" });
    if (!r.ok) return;
    const data: Campaign = await r.json();
    setCampaign(data);
    setForm({
      name: data.name,
      description: data.description || "",
      daily_start_hour: data.daily_start_hour ?? 9,
      daily_end_hour: data.daily_end_hour ?? 18,
      timezone: data.timezone || "Asia/Kolkata",
      target_call_count: data.target_call_count ?? 0,
    });
  }, [campaignId]);

  useEffect(() => { load(); }, [load]);

  const set = (field: keyof Campaign, value: string | number) =>
    setForm(f => ({ ...f, [field]: value }));

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const r = await fetch(`${bff}/campaigns/${campaignId}`, {
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      const data = await r.json();
      if (!r.ok) { setError(data.error || "Save failed"); return; }
      setCampaign(data);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch {
      setError("Network error");
    } finally {
      setSaving(false);
    }
  };

  if (!campaign) return <div className="py-10 text-center text-sm text-muted">Loading…</div>;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-sm font-semibold">Campaign Settings</h1>
          <p className="mt-0.5 text-xs text-muted">Calling window, target volume, and campaign details.</p>
        </div>
        <div className="flex items-center gap-2">
          {saved && <span className="text-xs text-green-600">Saved</span>}
          {error && <span className="text-xs text-red-500">{error}</span>}
          <button
            onClick={save}
            disabled={saving}
            className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground hover:opacity-90 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save Changes"}
          </button>
        </div>
      </div>

      <Card>
        <CardHeader><CardTitle>Calling Window</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-2 gap-4">
          <Field label="Daily Start Hour (24h)" hint="Earliest hour calls can be placed.">
            <input type="number" min={0} max={23} value={form.daily_start_hour ?? 9}
              onChange={e => set("daily_start_hour", parseInt(e.target.value))}
              className={inputCls} />
          </Field>
          <Field label="Daily End Hour (24h)" hint="Latest hour calls can be placed.">
            <input type="number" min={0} max={23} value={form.daily_end_hour ?? 18}
              onChange={e => set("daily_end_hour", parseInt(e.target.value))}
              className={inputCls} />
          </Field>
          <Field label="Timezone">
            <select value={form.timezone || "Asia/Kolkata"} onChange={e => set("timezone", e.target.value)} className={selectCls}>
              {TIMEZONES.map(tz => <option key={tz}>{tz}</option>)}
            </select>
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Campaign Details</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Field label="Campaign Name">
            <input type="text" value={form.name || ""} onChange={e => set("name", e.target.value)} className={inputCls} />
          </Field>
          <Field label="Description">
            <textarea value={form.description || ""} onChange={e => set("description", e.target.value)}
              className="w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-brand"
              rows={3} />
          </Field>
          <Field label="Target Call Count">
            <input type="number" min={0} value={form.target_call_count ?? 0}
              onChange={e => set("target_call_count", parseInt(e.target.value))}
              className={inputCls} />
          </Field>
          <Field label="Current Status">
            <input type="text" disabled value={campaign.status}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm opacity-60" />
          </Field>
        </CardContent>
      </Card>
    </div>
  );
}
