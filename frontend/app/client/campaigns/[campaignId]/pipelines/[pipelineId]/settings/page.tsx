"use client";

import { use, useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Breadcrumbs } from "@/components/client/breadcrumbs";

type ModelConfig = {
  model_config_id: string | null;
  stt_adapter: string;
  stt_model: string;
  llm_adapter: string;
  llm_model: string;
  llm_temperature: number;
  tts_adapter: string;
  tts_voice: string;
  config_level?: string;
  updated_at?: string;
};

const STT_ADAPTERS = ["whisper", "deepgram", "google_stt"];
const STT_MODELS: Record<string, string[]> = {
  whisper:    ["whisper-large-v3-turbo", "whisper-large-v3", "whisper-medium"],
  deepgram:   ["nova-2", "nova", "enhanced"],
  google_stt: ["latest_long", "latest_short"],
};
const LLM_ADAPTERS = ["vllm", "openai", "anthropic"];
const LLM_MODELS: Record<string, string[]> = {
  vllm:      ["qwen2.5-7b-instruct-fp8", "qwen2.5-14b-instruct", "mistral-7b-instruct"],
  openai:    ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
  anthropic: ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
};
const TTS_ADAPTERS = ["veena", "elevenlabs", "google_tts", "amazon_polly"];
const TTS_VOICES: Record<string, string[]> = {
  veena:       ["kavya", "priya", "aarav", "default"],
  elevenlabs:  ["Rachel", "Domi", "Bella", "Antoni"],
  google_tts:  ["en-IN-Neural2-A", "en-IN-Neural2-B", "en-IN-Standard-A"],
  amazon_polly:["Aditi", "Kajal", "Raveena"],
};

const inputCls = "w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-brand disabled:opacity-50";
const selectCls = inputCls;
const BFF = process.env.NEXT_PUBLIC_BFF_URL || "/bff";

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium">{label}</label>
      {hint && <p className="text-xs text-muted">{hint}</p>}
      {children}
    </div>
  );
}

export default function PipelineSettingsPage({
  params,
}: {
  params: Promise<{ campaignId: string; pipelineId: string }>;
}) {
  const { campaignId, pipelineId } = use(params);
  const [config, setConfig] = useState<ModelConfig | null>(null);
  const [form, setForm] = useState<Partial<ModelConfig>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const r = await fetch(`${BFF}/campaigns/${campaignId}/pipelines/${pipelineId}/model-config`, {
      credentials: "include",
    });
    if (!r.ok) { setError("Failed to load config"); return; }
    const data: ModelConfig = await r.json();
    setConfig(data);
    setForm({ ...data });
    setLoading(false);
  }, [campaignId, pipelineId]);

  useEffect(() => { load(); }, [load]);

  const set = (k: keyof ModelConfig, v: string | number) => {
    setForm((f) => {
      const next = { ...f, [k]: v };
      // Reset model when adapter changes
      if (k === "stt_adapter") next.stt_model = (STT_MODELS[v as string] ?? [])[0] ?? "";
      if (k === "llm_adapter") next.llm_model = (LLM_MODELS[v as string] ?? [])[0] ?? "";
      if (k === "tts_adapter") next.tts_voice  = (TTS_VOICES [v as string] ?? [])[0] ?? "";
      return next;
    });
    setSaved(false);
  };

  const save = async () => {
    setSaving(true); setError(null);
    try {
      const r = await fetch(`${BFF}/campaigns/${campaignId}/pipelines/${pipelineId}/model-config`, {
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error((body as { error?: string }).error ?? "Save failed");
      }
      const updated: ModelConfig = await r.json();
      setConfig(updated);
      setForm({ ...updated });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setSaving(false);
    }
  };

  const dirty = JSON.stringify(form) !== JSON.stringify(config);

  return (
    <div className="flex flex-col gap-6">
      <Breadcrumbs
        items={[
          { label: "Campaigns", href: "/client/campaigns" },
          { label: campaignId.slice(0, 8) + "…", href: `/client/campaigns/${campaignId}/pipelines` },
          { label: pipelineId.slice(0, 8) + "…", href: `/client/campaigns/${campaignId}/pipelines/${pipelineId}/leads` },
          { label: "Settings" },
        ]}
      />
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-sm font-semibold">Pipeline Settings</h1>
          <p className="mt-0.5 text-xs text-muted">
            Model configuration for this pipeline.
            {config?.config_level && config.config_level !== "pipeline" && (
              <span className="ml-1 text-warning">Currently inheriting {config.config_level}-level defaults — save to create a pipeline override.</span>
            )}
          </p>
        </div>
        <button
          onClick={save}
          disabled={!dirty || saving || loading}
          className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground disabled:opacity-40"
        >
          {saving ? "Saving…" : saved ? "Saved ✓" : "Save Changes"}
        </button>
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}
      {loading && <p className="text-xs text-muted">Loading…</p>}

      {!loading && (
        <>
          <Card>
            <CardHeader><CardTitle>Speech-to-Text (STT)</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-2 gap-4">
              <Field label="STT Adapter">
                <select className={selectCls} value={form.stt_adapter ?? ""} onChange={(e) => set("stt_adapter", e.target.value)}>
                  {STT_ADAPTERS.map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </Field>
              <Field label="STT Model">
                <select className={selectCls} value={form.stt_model ?? ""} onChange={(e) => set("stt_model", e.target.value)}>
                  {(STT_MODELS[form.stt_adapter ?? "whisper"] ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </Field>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Large Language Model (LLM)</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-2 gap-4">
              <Field label="LLM Adapter">
                <select className={selectCls} value={form.llm_adapter ?? ""} onChange={(e) => set("llm_adapter", e.target.value)}>
                  {LLM_ADAPTERS.map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </Field>
              <Field label="LLM Model">
                <select className={selectCls} value={form.llm_model ?? ""} onChange={(e) => set("llm_model", e.target.value)}>
                  {(LLM_MODELS[form.llm_adapter ?? "vllm"] ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </Field>
              <Field label="Temperature" hint="0 = deterministic, 2 = very creative. Default 0.3 works well for collections.">
                <input
                  type="number" min={0} max={2} step={0.05} className={inputCls}
                  value={form.llm_temperature ?? 0.3}
                  onChange={(e) => set("llm_temperature", parseFloat(e.target.value))}
                />
              </Field>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Text-to-Speech (TTS)</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-2 gap-4">
              <Field label="TTS Adapter">
                <select className={selectCls} value={form.tts_adapter ?? ""} onChange={(e) => set("tts_adapter", e.target.value)}>
                  {TTS_ADAPTERS.map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </Field>
              <Field label="Voice Profile">
                <select className={selectCls} value={form.tts_voice ?? ""} onChange={(e) => set("tts_voice", e.target.value)}>
                  {(TTS_VOICES[form.tts_adapter ?? "veena"] ?? []).map((v) => <option key={v} value={v}>{v}</option>)}
                </select>
              </Field>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
