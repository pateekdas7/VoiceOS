import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function FormField({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium">{label}</label>
      {hint ? <p className="text-xs text-muted">{hint}</p> : null}
      {children}
    </div>
  );
}

function DI({ value, placeholder, type = "text" }: { value?: string; placeholder?: string; type?: string }) {
  return (
    <input disabled type={type} defaultValue={value} placeholder={placeholder}
      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm opacity-60" />
  );
}

function DS({ value, options }: { value: string; options: string[] }) {
  return (
    <select disabled defaultValue={value}
      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm opacity-60">
      {options.map((o) => <option key={o}>{o}</option>)}
    </select>
  );
}

export default function PipelineSettingsPage() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-sm font-semibold">Pipeline Settings</h1>
          <p className="mt-0.5 text-xs text-muted">Model config, STT/LLM/TTS selection, retry policy, and schedule. Pipeline backend sprint required.</p>
        </div>
        <button disabled className="cursor-not-allowed rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground opacity-40">Save Changes</button>
      </div>
      <Card>
        <CardHeader><CardTitle>Pipeline Details</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-4">
          <FormField label="Pipeline Name"><DI placeholder="Pipeline name" /></FormField>
          <FormField label="Status"><DS value="DRAFT" options={["DRAFT", "ACTIVE", "PAUSED", "ARCHIVED"]} /></FormField>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Model Configuration</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-2 gap-4">
          <FormField label="STT Provider" hint="Speech-to-text engine for this pipeline.">
            <DS value="Deepgram Nova-2" options={["Deepgram Nova-2", "Whisper Large v3", "Google STT"]} />
          </FormField>
          <FormField label="LLM Provider" hint="Large language model for conversation logic.">
            <DS value="claude-opus-4-7" options={["claude-opus-4-7", "claude-sonnet-4-6", "gpt-4o"]} />
          </FormField>
          <FormField label="TTS Provider" hint="Text-to-speech voice synthesis.">
            <DS value="ElevenLabs" options={["ElevenLabs", "Google TTS", "Amazon Polly"]} />
          </FormField>
          <FormField label="Voice Profile" hint="Speaker persona to use for this pipeline.">
            <DS value="Default" options={["Default"]} />
          </FormField>
          <FormField label="System Prompt Version" hint="Prompt template version pinned for this pipeline.">
            <DI value="v1.0" />
          </FormField>
          <FormField label="Temperature" hint="LLM sampling temperature (0 = deterministic).">
            <DI value="0.3" type="number" />
          </FormField>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Retry &amp; Schedule</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-2 gap-4">
          <FormField label="Max Retries"><DI value="3" type="number" /></FormField>
          <FormField label="Retry Interval (hours)"><DI value="24" type="number" /></FormField>
          <FormField label="Daily Start Hour"><DI value="9" type="number" /></FormField>
          <FormField label="Daily End Hour"><DI value="18" type="number" /></FormField>
        </CardContent>
      </Card>
    </div>
  );
}
