import { SectionPlaceholder } from "@/components/client/section-placeholder";

export default function PipelineCRMPage() {
  return (
    <SectionPlaceholder
      title="Pipeline CRM"
      description="Customer records scoped to this pipeline's cohort."
      columns={["Customer", "CRM ID", "Contact", "Status"]}
      note="0 customers"
    />
  );
}
