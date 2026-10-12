"use client";

import { use, useEffect, useState } from "react";
import { CampaignLeadsView } from "@/components/client/campaign-leads-view";
import { listPipelines, type LocalPipeline } from "@/lib/local-pipelines";

export default function CampaignLeadsPage({ params }: { params: Promise<{ campaignId: string }> }) {
  const { campaignId } = use(params);
  const [pipelines, setPipelines] = useState<LocalPipeline[]>([]);

  useEffect(() => {
    listPipelines(campaignId).then(setPipelines).catch(() => setPipelines([]));
  }, [campaignId]);

  return (
    <CampaignLeadsView
      campaignId={campaignId}
      pipelines={pipelines.map(p => ({ pipeline_id: p.pipeline_id, name: p.name }))}
    />
  );
}
