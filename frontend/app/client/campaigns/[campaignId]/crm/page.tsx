import { SectionPlaceholder } from "@/components/client/section-placeholder";

export default function CampaignCRMPage() {
  return (
    <SectionPlaceholder
      title="Campaign CRM"
      description="src/services/crm.CustomerService has no campaign-scoped query (customers aren't owned by a campaign) -- this view would need a join through the campaign's audience cohort, which the tenant-wide CRM page doesn't do today. Tenant-wide CRM is already real at /client/crm."
      columns={["Customer", "CRM ID", "Contact", "Status"]}
      note="0 customers"
    />
  );
}
