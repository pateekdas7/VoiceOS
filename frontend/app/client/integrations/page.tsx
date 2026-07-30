import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const AVAILABLE_INTEGRATIONS = [
  {
    id: "webhook",
    name: "Outbound Webhook",
    description: "Push call events and lead outcomes to your own endpoints in real time.",
    category: "Custom",
    status: "available",
  },
  {
    id: "salesforce",
    name: "Salesforce CRM",
    description: "Sync contacts and call outcomes bidirectionally with Salesforce.",
    category: "CRM",
    status: "coming_soon",
  },
  {
    id: "zoho",
    name: "Zoho CRM",
    description: "Connect Zoho contacts for lead import and outcome sync.",
    category: "CRM",
    status: "coming_soon",
  },
  {
    id: "leadsquared",
    name: "LeadSquared",
    description: "Import leads from LeadSquared and push call dispositions back.",
    category: "CRM",
    status: "coming_soon",
  },
  {
    id: "slack",
    name: "Slack",
    description: "Receive alerts, HITL escalations, and daily summaries in Slack.",
    category: "Notifications",
    status: "coming_soon",
  },
  {
    id: "s3",
    name: "AWS S3",
    description: "Export call recordings and transcripts to your S3 bucket.",
    category: "Storage",
    status: "coming_soon",
  },
];

export default function IntegrationsPage() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Integrations</h1>
          <p className="mt-1 text-xs text-muted">
            Connect VoiceOS to your CRMs, webhooks, and notification channels.
          </p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Active Integrations</CardTitle>
        </CardHeader>
        <CardContent className="py-10 text-center text-sm text-muted">
          No integrations connected. Enable one from the catalog below.
        </CardContent>
      </Card>

      <div>
        <h2 className="mb-3 text-sm font-semibold">Integration Catalog</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {AVAILABLE_INTEGRATIONS.map((integration) => (
            <div
              key={integration.id}
              className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex h-9 w-9 items-center justify-center rounded-md border border-border bg-background text-lg">
                  {integration.name[0]}
                </div>
                <Badge tone={integration.status === "available" ? "neutral" : "neutral"}>
                  {integration.status === "available" ? "Available" : "Coming soon"}
                </Badge>
              </div>
              <div>
                <p className="text-sm font-medium">{integration.name}</p>
                <p className="mt-0.5 text-xs text-muted">{integration.description}</p>
              </div>
              <div className="flex items-center justify-between">
                <span className="rounded-full bg-background px-2 py-0.5 text-[11px] text-muted border border-border">
                  {integration.category}
                </span>
                <button
                  disabled
                  className="cursor-not-allowed rounded-md px-3 py-1 text-xs font-medium border border-border text-muted opacity-50"
                >
                  {integration.status === "available" ? "Configure" : "Notify me"}
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
