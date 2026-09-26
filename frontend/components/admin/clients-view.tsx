"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import {
  ApiError,
  createClient,
  listClients,
  reactivateClient,
  suspendClient,
  type Tenant,
} from "@/lib/api/clients";

const STATUS_TONE: Record<string, "neutral" | "healthy" | "warning" | "critical"> = {
  TRIAL: "neutral",
  SANDBOX: "neutral",
  PRODUCTION: "healthy",
  SUSPENDED: "critical",
  CANCELLED: "neutral",
  DELETING: "warning",
  DELETED: "neutral",
};

export function ClientsView() {
  const [clients, setClients] = useState<Tenant[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyTenantId, setBusyTenantId] = useState<string | null>(null);

  async function refresh() {
    try {
      const data = await listClients();
      setClients(data);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
    }
  }

  useEffect(() => {
    let cancelled = false;
    listClients()
      .then((data) => {
        if (cancelled) return;
        setClients(data);
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

  async function handleSuspendToggle(tenant: Tenant) {
    setActionError(null);
    setBusyTenantId(tenant.tenant_id);
    try {
      if (tenant.status === "SUSPENDED") {
        await reactivateClient(tenant.tenant_id);
      } else {
        await suspendClient(tenant.tenant_id);
      }
      await refresh();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Action failed.");
    } finally {
      setBusyTenantId(null);
    }
  }

  if (error) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
      </Card>
    );
  }

  if (clients === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading clients…</CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Clients</h1>
        <button
          type="button"
          onClick={() => setShowCreateForm((v) => !v)}
          className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground"
        >
          {showCreateForm ? "Cancel" : "New Client"}
        </button>
      </div>

      {showCreateForm ? (
        <CreateClientForm
          onCreated={() => {
            setShowCreateForm(false);
            refresh();
          }}
        />
      ) : null}

      {actionError ? <p className="text-sm text-status-critical">{actionError}</p> : null}

      <Card>
        <CardHeader>
          <CardTitle>{clients.length} client{clients.length === 1 ? "" : "s"}</CardTitle>
        </CardHeader>
        {clients.length === 0 ? (
          <CardContent className="py-10 text-center text-sm text-muted">No clients yet.</CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Client</TableHeaderCell>
                <TableHeaderCell>Slug</TableHeaderCell>
                <TableHeaderCell>Tier</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Created</TableHeaderCell>
                <TableHeaderCell>Actions</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {clients.map((tenant) => (
                <TableRow key={tenant.tenant_id}>
                  <TableCell className="font-medium">{tenant.display_name}</TableCell>
                  <TableCell className="text-muted">{tenant.slug}</TableCell>
                  <TableCell>{tenant.subscription_tier}</TableCell>
                  <TableCell>
                    <Badge tone={STATUS_TONE[tenant.status] ?? "neutral"}>{tenant.status}</Badge>
                  </TableCell>
                  <TableCell className="text-muted">{new Date(tenant.created_at).toLocaleDateString()}</TableCell>
                  <TableCell>
                    {tenant.status === "PRODUCTION" || tenant.status === "SUSPENDED" ? (
                      <button
                        type="button"
                        disabled={busyTenantId === tenant.tenant_id}
                        onClick={() => handleSuspendToggle(tenant)}
                        className="text-sm text-brand underline disabled:opacity-50"
                      >
                        {tenant.status === "SUSPENDED" ? "Reactivate" : "Suspend"}
                      </button>
                    ) : (
                      <span className="text-sm text-muted">—</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  );
}

function CreateClientForm({ onCreated }: { onCreated: () => void }) {
  const [slug, setSlug] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [tier, setTier] = useState("STARTER");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await createClient({ slug, display_name: displayName, subscription_tier: tier });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create client.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardContent>
        <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted" htmlFor="slug">
              Slug
            </label>
            <input
              id="slug"
              required
              pattern="[a-z0-9-]+"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
              placeholder="acme-collections"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted" htmlFor="display_name">
              Display name
            </label>
            <input
              id="display_name"
              required
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
              placeholder="Acme Collections"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted" htmlFor="tier">
              Tier
            </label>
            <select
              id="tier"
              value={tier}
              onChange={(e) => setTier(e.target.value)}
              className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
            >
              <option value="STARTER">STARTER</option>
              <option value="GROWTH">GROWTH</option>
              <option value="ENTERPRISE">ENTERPRISE</option>
              <option value="ENTERPRISE_PLUS">ENTERPRISE_PLUS</option>
            </select>
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
