"use client";

import { useRouter } from "next/navigation";
import { Breadcrumbs } from "@/components/client/breadcrumbs";
import { CreateCampaignForm } from "@/components/client/campaigns-view";

export default function NewCampaignPage() {
  const router = useRouter();

  return (
    <div className="flex flex-col gap-4">
      <Breadcrumbs items={[{ label: "Campaigns", href: "/client/campaigns" }, { label: "Create Campaign" }]} />
      <h1 className="text-lg font-semibold">Create Campaign</h1>
      <CreateCampaignForm onCreated={() => router.push("/client/campaigns")} />
    </div>
  );
}
