import { SectionPlaceholder } from "@/components/client/section-placeholder";

export default function PipelineExecutionHistoryPage() {
  return (
    <SectionPlaceholder
      title="Execution History"
      description="Every run of this pipeline's execution graph -- the GET /pipelines/{id}/execution-graph endpoint ADR-005 §14 designs does not exist yet."
      columns={["Run", "Started", "Duration", "Outcome"]}
      note="0 runs"
    />
  );
}
