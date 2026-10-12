"use client";

import { use } from "react";
import { PipelineLeadsView } from "@/components/client/pipeline-leads-view";

export default function PipelineLeadsPage({ params }: { params: Promise<{ campaignId: string; pipelineId: string }> }) {
  const { pipelineId } = use(params);
  return <PipelineLeadsView pipelineId={pipelineId} />;
}
