import { SectionPlaceholder } from "@/components/client/section-placeholder";

export default function NewPipelinePage() {
  return (
    <SectionPlaceholder
      title="Create Pipeline"
      description="Pipeline creation form shell -- ready to be wired once the Pipeline entity (model/migration/repository/service/BFF route) exists."
      columns={[]}
      note="Pipeline creation form"
    />
  );
}
