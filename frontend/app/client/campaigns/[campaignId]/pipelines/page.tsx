import { SectionPlaceholder } from "@/components/client/section-placeholder";

export default function PipelinesPage() {
  return (
    <SectionPlaceholder
      title="Pipelines"
      description="A campaign's execution graphs -- ADR-005 §4.3/§6.3 designs Pipeline as a new entity (model, table, repository, service, BFF routes) that does not exist in this codebase yet. This is the frontend shell only; wiring it is its own dedicated sprint."
      columns={["Pipeline", "Status", "Leads", "Last Run", "Actions"]}
      primaryAction="Create Pipeline"
      note="0 pipelines"
    />
  );
}
