"use client";

import { useRouter } from "next/navigation";
import { use, useState } from "react";
import { Breadcrumbs } from "@/components/client/breadcrumbs";
import { Card, CardContent } from "@/components/ui/card";
import { createPipeline } from "@/lib/local-pipelines";

export default function NewPipelinePage({ params }: { params: Promise<{ campaignId: string }> }) {
  const { campaignId } = use(params);
  const router = useRouter();
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    const pipeline = await createPipeline(campaignId, name);
    router.push(`/client/campaigns/${campaignId}/pipelines/${pipeline.pipeline_id}`);
  }

  return (
    <div className="flex flex-col gap-4">
      <Breadcrumbs
        items={[
          { label: "Campaigns", href: "/client/campaigns" },
          { label: "Pipelines", href: `/client/campaigns/${campaignId}/pipelines` },
          { label: "Create Pipeline" },
        ]}
      />
      <h1 className="text-lg font-semibold">Create Pipeline</h1>
      <Card>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted" htmlFor="name">
                Name
              </label>
              <input
                id="name"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
                placeholder="First Contact Sequence"
              />
            </div>
            <button
              type="submit"
              disabled={submitting || !name.trim()}
              className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground disabled:opacity-60"
            >
              {submitting ? "Creating…" : "Create"}
            </button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
