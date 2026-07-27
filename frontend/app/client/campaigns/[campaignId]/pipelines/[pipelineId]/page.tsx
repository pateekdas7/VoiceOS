import { redirect } from "next/navigation";

export default async function PipelineOverviewPage({
  params,
}: {
  params: Promise<{ campaignId: string; pipelineId: string }>;
}) {
  const { campaignId, pipelineId } = await params;
  redirect(`/client/campaigns/${campaignId}/pipelines/${pipelineId}/leads`);
}
