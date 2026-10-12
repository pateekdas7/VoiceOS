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
  scheduled_start: string | null;
  scheduled_end: string | null;
  allowed_weekdays: number[];
  excluded_dates: string[];
  max_attempts: number;
  retry_interval_hours: number;
};

const bff = process.env.NEXT_PUBLIC_BFF_URL || "/bff";
const TIMEZONES = ["Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "UTC", "America/New_York", "Europe/London"];
const WEEKDAYS: { iso: number; label: string; long: string }[] = [
  { iso: 1, label: "Mon", long: "Monday" },
  { iso: 2, label: "Tue", long: "Tuesday" },
  { iso: 3, label: "Wed", long: "Wednesday" },
  { iso: 4, label: "Thu", long: "Thursday" },
  { iso: 5, label: "Fri", long: "Friday" },
  { iso: 6, label: "Sat", long: "Saturday" },
  { iso: 7, label: "Sun", long: "Sunday" },
];

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

function normalizeDateString(raw: string | null | undefined): string {
  if (!raw) return "";
  // Postgres returns YYYY-MM-DD or full ISO — clip to date.
  return String(raw).slice(0, 10);
}

export default function CampaignSettingsPage({
  params,
}: {
  params: Promise<{ campaignId: string }>;
}) {
  const { campaignId } = use(params);
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [form, setForm] = useState<Partial<Campaign>>({});
  const [newExcluded, setNewExcluded] = useState<string>("");
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
      scheduled_start: data.scheduled_start ? String(data.scheduled_start).slice(0, 16) : "",
      scheduled_end: data.scheduled_end ? String(data.scheduled_end).slice(0, 16) : "",
      allowed_weekdays: (data.allowed_weekdays || [1, 2, 3, 4, 5, 6, 7]).map(Number),
      excluded_dates: (data.excluded_dates || []).map(normalizeDateString),
      max_attempts: data.max_attempts ?? 3,
      retry_interval_hours: data.retry_interval_hours ?? 24,
    });
  }, [campaignId]);

  useEffect(() => { load(); }, [load]);

  const set = <K extends keyof Campaign>(field: K, value: Campaign[K]) =>
    setForm(f => ({ ...f, [field]: value }));

  const toggleWeekday = (iso: number) => {
    const cur = new Set(form.allowed_weekdays || []);
    if (cur.has(iso)) cur.delete(iso); else cur.add(iso);
    set("allowed_weekdays", Array.from(cur).sort((a, b) => a - b) as number[]);
  };

  const addExcludedDate = () => {
    if (!newExcluded || !/^\d{4}-\d{2}-\d{2}$/.test(newExcluded)) return;
    const cur = form.excluded_dates || [];
    if (cur.includes(newExcluded)) { setNewExcluded(""); return; }
    set("excluded_dates", [...cur, newExcluded].sort() as string[]);
    setNewExcluded("");
  };

  const removeExcludedDate = (d: string) => {
    set("excluded_dates", (form.excluded_dates || []).filter(x => x !== d) as string[]);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      // Convert datetime-local to ISO with timezone; empty string → null so
      // COALESCE keeps existing value untouched.
      const payload = {
        ...form,
        scheduled_start: form.scheduled_start ? new Date(form.scheduled_start).toISOString() : null,
        scheduled_end: form.scheduled_end ? new Date(form.scheduled_end).toISOString() : null,
      };
      const r = await fetch(`${bff}/campaigns/${campaignId}`, {
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
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

  const allowed = new Set(form.allowed_weekdays || []);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-sm font-semibold">Campaign Settings</h1>
          <p className="mt-0.5 text-xs text-muted">Calling window, weekly schedule, and exclusions.</p>
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
        <CardHeader><CardTitle>Daily Calling Window</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-3 gap-4">
          <Field label="Daily Start Hour (24h)" hint="Earliest hour calls can be placed.">
            <input type="number" min={0} max={23} value={form.daily_start_hour ?? 9}
              onChange={e => set("daily_start_hour", parseInt(e.target.value))}
              className={inputCls} />
          </Field>
          <Field label="Daily End Hour (24h)" hint="Latest hour calls can be placed (exclusive).">
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
        <CardHeader><CardTitle>Weekly Schedule</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-xs text-muted">Days of the week when this campaign is allowed to place calls. Un-selected days are skipped even if within the daily hour window.</p>
          <div className="flex flex-wrap gap-2">
            {WEEKDAYS.map(w => {
              const on = allowed.has(w.iso);
              return (
                <button
                  key={w.iso}
                  type="button"
                  onClick={() => toggleWeekday(w.iso)}
                  className={`rounded-full border px-3 py-1 text-xs font-medium transition ${
                    on
                      ? "border-brand bg-brand text-brand-foreground"
                      : "border-border bg-background text-muted hover:border-brand"
                  }`}
                  aria-pressed={on}
                  title={w.long}
                >
                  {w.label}
                </button>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Excluded Dates</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-xs text-muted">Specific dates on which no calls are placed (public holidays, blackout windows, etc.). Overrides the weekly schedule.</p>
          <div className="flex items-center gap-2">
            <input
              type="date"
              value={newExcluded}
              onChange={e => setNewExcluded(e.target.value)}
              className={`${inputCls} w-auto`}
            />
            <button
              type="button"
              onClick={addExcludedDate}
              disabled={!newExcluded}
              className="rounded-md border border-border px-3 py-2 text-sm hover:border-brand disabled:opacity-50"
            >
              Add
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {(form.excluded_dates || []).length === 0 && (
              <span className="text-xs text-muted">No excluded dates.</span>
            )}
            {(form.excluded_dates || []).map(d => (
              <span key={d} className="inline-flex items-center gap-1.5 rounded-md border border-border bg-surface px-2 py-0.5 text-xs">
                {d}
                <button
                  type="button"
                  onClick={() => removeExcludedDate(d)}
                  className="text-muted hover:text-red-500"
                  aria-label={`Remove ${d}`}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Campaign Window (Optional)</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-2 gap-4">
          <Field label="Start (date & time)" hint="Absolute earliest moment calls may be placed. Leave blank for no lower bound.">
            <input type="datetime-local" value={form.scheduled_start || ""} onChange={e => set("scheduled_start", e.target.value as unknown as string)} className={inputCls} />
          </Field>
          <Field label="End (date & time)" hint="Absolute latest moment calls may be placed. Leave blank for no upper bound.">
            <input type="datetime-local" value={form.scheduled_end || ""} onChange={e => set("scheduled_end", e.target.value as unknown as string)} className={inputCls} />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Retry Policy</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-2 gap-4">
          <Field label="Max Attempts" hint="Maximum call attempts per lead.">
            <input type="number" min={1} max={20} value={form.max_attempts ?? 3}
              onChange={e => set("max_attempts", parseInt(e.target.value))}
              className={inputCls} />
          </Field>
          <Field label="Retry Interval (hours)" hint="Hours to wait before retrying a lead.">
            <input type="number" min={1} max={168} value={form.retry_interval_hours ?? 24}
              onChange={e => set("retry_interval_hours", parseInt(e.target.value))}
              className={inputCls} />
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
