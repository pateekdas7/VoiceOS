"use client";

import { useEffect, useReducer, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, bffDel, bffGet, bffPut } from "@/lib/api/fetch-client";

// ── Types ─────────────────────────────────────────────────────────────────────

type LSConfig = {
  cred_id: string;
  api_base_url: string;
  is_active: boolean;
  access_key_preview: string;
  updated_at: string;
} | null;

type LSState =
  | { kind: "loading" }
  | { kind: "idle"; config: LSConfig }
  | { kind: "configuring"; config: LSConfig }
  | { kind: "saving" }
  | { kind: "error"; message: string; config: LSConfig };

type LSAction =
  | { type: "loaded"; config: LSConfig }
  | { type: "load_failed"; message: string }
  | { type: "open_form" }
  | { type: "cancel" }
  | { type: "save_start" }
  | { type: "save_done"; config: LSConfig }
  | { type: "save_failed"; message: string; config: LSConfig }
  | { type: "disconnect_done" };

function lsReducer(state: LSState, action: LSAction): LSState {
  switch (action.type) {
    case "loaded":      return { kind: "idle", config: action.config };
    case "load_failed": return { kind: "error", message: action.message, config: null };
    case "open_form":   return state.kind === "idle" ? { kind: "configuring", config: state.config } : state;
    case "cancel":      return state.kind === "configuring" ? { kind: "idle", config: state.config } : state;
    case "save_start":  return { kind: "saving" };
    case "save_done":   return { kind: "idle", config: action.config };
    case "save_failed":
      return { kind: "error", message: action.message, config: action.config };
    case "disconnect_done": return { kind: "idle", config: null };
    default: return state;
  }
}

// ── LeadSquared Card ──────────────────────────────────────────────────────────

function LeadSquaredCard() {
  const [state, dispatch] = useReducer(lsReducer, { kind: "loading" });
  const [accessKey, setAccessKey] = useState("");
  const [secretKey, setSecretKey] = useState("");
  const [apiBase, setApiBase] = useState("https://api.leadsquared.com");
  const [disconnecting, setDisconnecting] = useState(false);

  useEffect(() => {
    bffGet<{ config: LSConfig }>("/admin/crm/leadsquared")
      .then(({ config }) => dispatch({ type: "loaded", config }))
      .catch((err) => dispatch({ type: "load_failed", message: err instanceof ApiError ? err.message : "Load failed" }));
  }, []);

  function handleOpenForm() {
    const config = state.kind === "idle" ? state.config : null;
    if (config?.api_base_url) setApiBase(config.api_base_url);
    setAccessKey("");
    setSecretKey("");
    dispatch({ type: "open_form" });
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    const prevConfig = state.kind === "configuring" ? state.config : null;
    dispatch({ type: "save_start" });
    try {
      const { config } = await bffPut<{ config: LSConfig }>("/admin/crm/leadsquared", {
        access_key: accessKey,
        secret_key: secretKey,
        api_base_url: apiBase,
      });
      dispatch({ type: "save_done", config });
    } catch (err) {
      dispatch({
        type: "save_failed",
        message: err instanceof ApiError ? err.message : "Save failed",
        config: prevConfig,
      });
    }
  }

  async function handleDisconnect() {
    if (!confirm("Disconnect LeadSquared? All sync jobs will stop.")) return;
    setDisconnecting(true);
    try {
      await bffDel("/admin/crm/leadsquared");
      dispatch({ type: "disconnect_done" });
    } catch (err) {
      dispatch({
        type: "save_failed",
        message: err instanceof ApiError ? err.message : "Disconnect failed",
        config: state.kind === "idle" ? state.config : null,
      });
    } finally {
      setDisconnecting(false);
    }
  }

  const config = state.kind === "idle" || state.kind === "configuring" || state.kind === "error"
    ? state.config
    : null;
  const isConnected = config?.is_active === true;
  const isConfiguring = state.kind === "configuring";
  const isSaving = state.kind === "saving";
  const hasError = state.kind === "error";

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>LeadSquared</CardTitle>
          <p className="mt-1 text-xs text-muted">
            Import leads from LeadSquared and push call dispositions, PTPs, and settlements back.
          </p>
        </div>
        {state.kind === "loading" ? (
          <Badge tone="neutral">Loading\u2026</Badge>
        ) : isConnected ? (
          <Badge tone="healthy">Connected</Badge>
        ) : (
          <Badge tone="neutral">Not connected</Badge>
        )}
      </CardHeader>

      <CardContent className="flex flex-col gap-4">
        {hasError && (
          <p className="text-sm text-status-critical">{(state as { kind: "error"; message: string }).message}</p>
        )}

        {isConnected && !isConfiguring && (
          <div className="rounded-md border border-border bg-background p-3 text-xs">
            <p className="font-medium">Access key</p>
            <p className="mt-0.5 font-mono text-muted">{config!.access_key_preview}</p>
            <p className="mt-2 font-medium">API base</p>
            <p className="mt-0.5 font-mono text-muted">{config!.api_base_url}</p>
            <p className="mt-2 text-muted">Last updated {new Date(config!.updated_at).toLocaleString()}</p>
          </div>
        )}

        {isConfiguring || !isConnected ? (
          isConfiguring || state.kind !== "loading" ? (
            <form onSubmit={handleSave} className="flex flex-col gap-3">
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-muted" htmlFor="ls-access">Access Key</label>
                <input
                  id="ls-access"
                  required
                  autoComplete="off"
                  value={accessKey}
                  onChange={(e) => setAccessKey(e.target.value)}
                  placeholder="ak_xxxxxxxxxxxxxxxx"
                  className="rounded-md border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand/50"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-muted" htmlFor="ls-secret">Secret Key</label>
                <input
                  id="ls-secret"
                  type="password"
                  required
                  autoComplete="new-password"
                  value={secretKey}
                  onChange={(e) => setSecretKey(e.target.value)}
                  placeholder="sk_xxxxxxxxxxxxxxxx"
                  className="rounded-md border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand/50"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-muted" htmlFor="ls-base">API Base URL</label>
                <input
                  id="ls-base"
                  value={apiBase}
                  onChange={(e) => setApiBase(e.target.value)}
                  className="rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand/50"
                />
              </div>
              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={isSaving}
                  className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground disabled:opacity-60"
                >
                  {isSaving ? "Saving\u2026" : isConnected ? "Update Credentials" : "Connect"}
                </button>
                {isConfiguring && (
                  <button
                    type="button"
                    onClick={() => dispatch({ type: "cancel" })}
                    className="rounded-md border border-border px-3 py-1.5 text-sm"
                  >
                    Cancel
                  </button>
                )}
              </div>
            </form>
          ) : null
        ) : null}

        {isConnected && !isConfiguring && (
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleOpenForm}
              className="text-sm text-brand underline"
            >
              Update credentials
            </button>
            <button
              type="button"
              disabled={disconnecting}
              onClick={handleDisconnect}
              className="text-sm text-status-critical underline disabled:opacity-50"
            >
              {disconnecting ? "Disconnecting\u2026" : "Disconnect"}
            </button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Coming-Soon Card ──────────────────────────────────────────────────────────

function ComingSoonCard({
  name,
  description,
  category,
}: {
  name: string;
  description: string;
  category: string;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>{name}</CardTitle>
          <p className="mt-0.5 text-xs text-muted">{category}</p>
          <p className="mt-1 text-xs text-muted">{description}</p>
        </div>
        <Badge tone="neutral">Coming soon</Badge>
      </CardHeader>
    </Card>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

const COMING_SOON = [
  {
    name: "Outbound Webhook",
    description: "Push call events and lead outcomes to your own endpoints in real time.",
    category: "Custom",
  },
  {
    name: "Salesforce CRM",
    description: "Sync contacts and call outcomes bidirectionally with Salesforce.",
    category: "CRM",
  },
  {
    name: "Zoho CRM",
    description: "Connect Zoho contacts for lead import and outcome sync.",
    category: "CRM",
  },
  {
    name: "Slack",
    description: "Receive alerts, HITL escalations, and daily summaries in Slack.",
    category: "Notifications",
  },
  {
    name: "AWS S3",
    description: "Export call recordings and transcripts to your S3 bucket.",
    category: "Storage",
  },
];

export default function IntegrationsPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold">Integrations</h1>
        <p className="mt-1 text-xs text-muted">Connect VoiceOS with your existing tools and data sources.</p>
      </div>

      <div>
        <h2 className="mb-3 text-sm font-semibold text-muted">CRM</h2>
        <LeadSquaredCard />
      </div>

      <div>
        <h2 className="mb-3 text-sm font-semibold text-muted">Coming soon</h2>
        <div className="flex flex-col gap-3">
          {COMING_SOON.map((i) => (
            <ComingSoonCard key={i.name} {...i} />
          ))}
        </div>
      </div>
    </div>
  );
}
