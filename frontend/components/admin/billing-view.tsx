"use client";

import { useEffect, useState } from "react";
import { TenantSelect } from "@/components/admin/tenant-select";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, createSubscription, generateInvoice, getSubscription, type Subscription } from "@/lib/api/admin-ops";

const TIERS = ["TRIAL", "STARTER", "GROWTH", "ENTERPRISE", "ENTERPRISE_PLUS"];

export function BillingView() {
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [subscription, setSubscription] = useState<Subscription | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [tier, setTier] = useState(TIERS[1]);
  const [busy, setBusy] = useState(false);
  const [invoiceMsg, setInvoiceMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    let cancelled = false;
    getSubscription(tenantId)
      .then((data) => {
        if (cancelled) return;
        setSubscription(data);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
      });
    return () => {
      cancelled = true;
    };
  }, [tenantId]);

  async function handleCreate() {
    if (!tenantId) return;
    setBusy(true);
    try {
      const created = await createSubscription(tenantId, tier);
      setSubscription(created);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create subscription.");
    } finally {
      setBusy(false);
    }
  }

  async function handleGenerateInvoice() {
    if (!tenantId) return;
    setBusy(true);
    setInvoiceMsg(null);
    try {
      const now = new Date();
      const start = new Date(now.getFullYear(), now.getMonth(), 1).toISOString();
      const end = new Date(now.getFullYear(), now.getMonth() + 1, 0).toISOString();
      await generateInvoice(tenantId, start, end);
      setInvoiceMsg("Invoice generated for the current month.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not generate invoice.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Billing</h1>
        <TenantSelect value={tenantId} onChange={setTenantId} />
      </div>

      {error ? <p className="text-sm text-status-critical">{error}</p> : null}
      {invoiceMsg ? <p className="text-sm text-status-healthy">{invoiceMsg}</p> : null}

      {subscription === undefined ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted">
            {tenantId ? "Loading subscription…" : "Select a client."}
          </CardContent>
        </Card>
      ) : subscription === null ? (
        <Card>
          <CardHeader>
            <CardTitle>No subscription yet</CardTitle>
          </CardHeader>
          <CardContent className="flex items-end gap-3">
            <select
              value={tier}
              onChange={(e) => setTier(e.target.value)}
              className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
            >
              {TIERS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={busy}
              onClick={handleCreate}
              className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground disabled:opacity-60"
            >
              Create Subscription
            </button>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              {subscription.tier} <Badge tone={subscription.is_active ? "healthy" : "critical"}>{subscription.is_active ? "Active" : "Inactive"}</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <p className="text-sm text-muted">
              Contract start: {new Date(subscription.contract_start).toLocaleDateString()} · Currency: {subscription.currency}
            </p>
            <button
              type="button"
              disabled={busy}
              onClick={handleGenerateInvoice}
              className="w-fit rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground disabled:opacity-60"
            >
              Generate Invoice for Current Month
            </button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
