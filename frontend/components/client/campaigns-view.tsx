"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import {
  ApiError,
  createCampaign,
  listCampaigns,
  runLifecycleAction,
  type Campaign,
  type LifecycleAction,
} from "@/lib/api/campaigns";

const STATUS_TONE: Record<Campaign["status"], "neutral" | "healthy" | "warning" | "critical"> = {
  DRAFT: "neutral",
  REVIEW: "warning",
  APPROVED: "neutral",
  ACTIVE: "healthy",
  PAUSED: "warning",
  COMPLETED: "neutral",
  ARCHIVED: "neutral",
};

const NEXT_ACTION: Partial<Record<Campaign["status"], { action: LifecycleAction; label: string }>> = {
  DRAFT: { action: "submit-for-review", label: "Submit for Review" },
  REVIEW: { action: "approve", label: "Approve" },
  APPROVED: { action: "start", label: "Start" },
  ACTIVE: { action: "pause", label: "Pause" },
  PAUSED: { action: "resume", label: "Resume" },
  COMPLETED: { action: "archive", label: "Archive" },
};

export function CampaignsView() {
  const [campaigns, setCampaigns] = useState<Campaign[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listCampaigns()
      .then((data) => {
        if (cancelled) return;
        setCampaigns(data);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function refresh() {
    try {
      setCampaigns(await listCampaigns());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
    }
  }

  async function handleAction(campaign: Campaign) {
    const next = NEXT_ACTION[campaign.status];
    if (!next) return;
    setActionError(null);
    setBusyId(campaign.campaign_id);
    try {
      const body = next.action === "start" ? { target_call_count: 100 } : undefined;
      await runLifecycleAction(campaign.campaign_id, next.action, body);
      await refresh();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Action failed.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleComplete(campaign: Campaign) {
    setActionError(null);
    setBusyId(campaign.campaign_id);
    try {
      await runLifecycleAction(campaign.campaign_id, "complete");
      await refresh();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Action failed.");
    } finally {
      setBusyId(null);
    }
  }

  if (error) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
      </Card>
    );
  }

  if (campaigns === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading campaigns…</CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Campaigns</h1>
        <button
          type="button"
          onClick={() => setShowCreateForm((v) => !v)}
          className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground"
        >
          {showCreateForm ? "Cancel" : "New Campaign"}
        </button>
      </div>

      {showCreateForm ? (
        <CreateCampaignForm
          onCreated={() => {
            setShowCreateForm(false);
            refresh();
          }}
        />
      ) : null}

      {actionError ? <p className="text-sm text-status-critical">{actionError}</p> : null}

      <Card>
        <CardHeader>
          <CardTitle>
            {campaigns.length} campaign{campaigns.length === 1 ? "" : "s"}
          </CardTitle>
        </CardHeader>
        {campaigns.length === 0 ? (
          <CardContent className="py-10 text-center text-sm text-muted">No campaigns yet.</CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Campaign</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Calls</TableHeaderCell>
                <TableHeaderCell>Created</TableHeaderCell>
                <TableHeaderCell>Actions</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {campaigns.map((campaign) => {
                const next = NEXT_ACTION[campaign.status];
                return (
                  <TableRow key={campaign.campaign_id}>
                    <TableCell className="font-medium">{campaign.name}</TableCell>
                    <TableCell>
                      <Badge tone={STATUS_TONE[campaign.status]}>{campaign.status}</Badge>
                    </TableCell>
                    <TableCell className="text-muted">
                      {campaign.completed_call_count} / {campaign.target_call_count || "—"}
                    </TableCell>
                    <TableCell className="text-muted">
                      {new Date(campaign.created_at).toLocaleDateString()}
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-3">
                        {next ? (
                          <button
                            type="button"
                            disabled={busyId === campaign.campaign_id}
                            onClick={() => handleAction(campaign)}
                            className="text-sm text-brand underline disabled:opacity-50"
                          >
                            {next.label}
                          </button>
                        ) : null}
                        {campaign.status === "ACTIVE" || campaign.status === "PAUSED" ? (
                          <button
                            type="button"
                            disabled={busyId === campaign.campaign_id}
                            onClick={() => handleComplete(campaign)}
                            className="text-sm text-brand underline disabled:opacity-50"
                          >
                            Complete
                          </button>
                        ) : null}
                        {!next && campaign.status !== "ACTIVE" && campaign.status !== "PAUSED" ? (
                          <span className="text-sm text-muted">—</span>
                        ) : null}
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  );
}

export function CreateCampaignForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await createCampaign({ name, description });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create campaign.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
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
              placeholder="Q3 EMI Reminders"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted" htmlFor="description">
              Description
            </label>
            <input
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
              placeholder="Optional"
            />
          </div>
          <button
            type="submit"
            disabled={submitting}
            className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground disabled:opacity-60"
          >
            {submitting ? "Creating…" : "Create"}
          </button>
          {error ? <p className="w-full text-sm text-status-critical">{error}</p> : null}
        </form>
      </CardContent>
    </Card>
  );
}
