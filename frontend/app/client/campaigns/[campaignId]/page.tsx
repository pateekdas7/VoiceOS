import { redirect } from "next/navigation";

export default async function CampaignOverviewPage({ params }: { params: Promise<{ campaignId: string }> }) {
  const { campaignId } = await params;
  redirect(`/client/campaigns/${campaignId}/pipelines`);
}
