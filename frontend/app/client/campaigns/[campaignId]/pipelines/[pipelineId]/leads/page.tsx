import { SectionPlaceholder } from "@/components/client/section-placeholder";

export default function PipelineLeadsPage() {
  return (
    <SectionPlaceholder
      title="Leads"
      description="This pipeline's own lead cohort, once Pipeline exists as a real entity re-keyed off CampaignAudienceMember."
      columns={["Customer", "Contact", "Stage", "DND", "Included"]}
      note="0 leads"
    />
  );
}
