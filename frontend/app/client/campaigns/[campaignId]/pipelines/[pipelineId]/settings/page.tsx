import { SectionPlaceholder } from "@/components/client/section-placeholder";

export default function PipelineSettingsPage() {
  return (
    <SectionPlaceholder
      title="Pipeline Settings"
      description="Per-pipeline ModelConfig/PromptVersion tier, retry policy, and schedule -- ADR-005 §14 scopes re-keying ModelConfig/PromptVersion to a pipeline_id tier as a cross-cutting change belonging to the dedicated Pipeline sprint, not this frontend-structure pass."
      columns={[]}
      note="Pipeline settings form"
    />
  );
}
