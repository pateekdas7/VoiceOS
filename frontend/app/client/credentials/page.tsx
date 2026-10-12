"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { ApiError, bffGet, bffPost } from "@/lib/api/fetch-client";

type ApiKey = {
  api_key_id: string;
  role: string;
  scopes: string[];
  plan_tier: string;
  is_revoked: boolean;
  created_at: string;
  expires_at: string | null;
  revoked_at: string | null;
};

export default function CredentialsPage() {
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [newKeyMsg, setNewKeyMsg] = useState<string | null>(null);

  async function refresh() {
    try {
      setKeys(await bffGet<ApiKey[]>("/api-keys"));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach backend.");
    }
  }

  useEffect(() => {
    let cancelled = false;
    bffGet<ApiKey[]>("/api-keys")
      .then((data) => { if (!cancelled) setKeys(data); })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not reach backend."); });
    return () => { cancelled = true; };
  }, []);

  async function handleRevoke(id: string) {
    setBusyId(id);
    try {
      await bffPost(`/api-keys/${id}/revoke`);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not revoke key.");
    } finally { setBusyId(null); }
  }

  async function handleRotate(id: string) {
    setBusyId(`rotate-${id}`);
    try {
      const result = await bffPost<{ api_key_id: string; raw_key: string }>(`/api-keys/${id}/rotate`);
      setNewKeyMsg(`Rotated key — new raw key (shown once): ${result.raw_key}`);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not rotate key.");
    } finally { setBusyId(null); }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Credentials</h1>
          <p className="mt-1 text-xs text-muted">API keys for programmatic access to the VoiceOS platform APIs.</p>
        </div>
        <button
          type="button"
          onClick={() => setShowForm((v) => !v)}
          className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground"
        >
          {showForm ? "Cancel" : "Generate Key"}
        </button>
      </div>

      {newKeyMsg && (
        <div className="rounded-md border border-border bg-background p-3 text-xs font-mono break-all">
          {newKeyMsg}
          <button type="button" onClick={() => setNewKeyMsg(null)} className="ml-2 text-muted underline">Dismiss</button>
        </div>
      )}

      {showForm && (
        <GenerateKeyForm
          onCreated={(rawKey) => {
            setShowForm(false);
            setNewKeyMsg(`New API key (shown once): ${rawKey}`);
            refresh();
          }}
        />
      )}

      {error && <p className="text-sm text-status-critical">{error}</p>}

      <Card>
        <CardHeader>
          <CardTitle>API Keys {keys !== null && `(${keys.length})`}</CardTitle>
        </CardHeader>
        {keys === null ? (
          <CardContent className="py-10 text-center text-sm text-muted">Loading\u2026</CardContent>
        ) : keys.length === 0 ? (
          <CardContent className="py-12 text-center text-sm text-muted">
            No API keys yet. Generate a key to access VoiceOS APIs programmatically.
          </CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>ID</TableHeaderCell>
                <TableHeaderCell>Role</TableHeaderCell>
                <TableHeaderCell>Tier</TableHeaderCell>
                <TableHeaderCell>Created</TableHeaderCell>
                <TableHeaderCell>Expires</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Actions</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {keys.map((k) => (
                <TableRow key={k.api_key_id}>
                  <TableCell className="font-mono text-xs">{k.api_key_id.slice(0, 8)}\u2026</TableCell>
                  <TableCell>{k.role || "\u2014"}</TableCell>
                  <TableCell>{k.plan_tier || "\u2014"}</TableCell>
                  <TableCell className="text-muted">{new Date(k.created_at).toLocaleDateString()}</TableCell>
                  <TableCell className="text-muted">{k.expires_at ? new Date(k.expires_at).toLocaleDateString() : "Never"}</TableCell>
                  <TableCell>
                    <Badge tone={k.is_revoked ? "critical" : "healthy"}>{k.is_revoked ? "Revoked" : "Active"}</Badge>
                  </TableCell>
                  <TableCell>
                    {k.is_revoked ? (
                      <span className="text-sm text-muted">\u2014</span>
                    ) : (
                      <div className="flex gap-3">
                        <button
                          type="button"
                          disabled={busyId === `rotate-${k.api_key_id}`}
                          onClick={() => handleRotate(k.api_key_id)}
                          className="text-sm text-brand underline disabled:opacity-50"
                        >Rotate</button>
                        <button
                          type="button"
                          disabled={busyId === k.api_key_id}
                          onClick={() => handleRevoke(k.api_key_id)}
                          className="text-sm text-status-critical underline disabled:opacity-50"
                        >Revoke</button>
                      </div>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>

      <Card>
        <CardHeader><CardTitle>Security best practices</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-2 text-sm text-muted">
          <p>\u2022 Never commit API keys to source control. Use environment variables or a secrets manager.</p>
          <p>\u2022 Rotate keys every 90 days or immediately after a suspected exposure.</p>
          <p>\u2022 Use the minimum required scopes \u2014 read-only keys cannot modify campaigns or leads.</p>
          <p>\u2022 All API key activity is recorded in Audit Logs.</p>
        </CardContent>
      </Card>
    </div>
  );
}

function GenerateKeyForm({ onCreated }: { onCreated: (rawKey: string) => void }) {
  const [role, setRole] = useState("");
  const [tier, setTier] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const result = await bffPost<{ api_key_id: string; raw_key: string }>("/api-keys", {
        role,
        plan_tier: tier,
        scopes: [],
      });
      onCreated(result.raw_key);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not generate key.");
    } finally { setSubmitting(false); }
  }

  return (
    <Card>
      <CardContent>
        <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted" htmlFor="role">Role (optional)</label>
            <input id="role" value={role} onChange={(e) => setRole(e.target.value)}
              placeholder="e.g. read-only"
              className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted" htmlFor="tier">Plan tier (optional)</label>
            <input id="tier" value={tier} onChange={(e) => setTier(e.target.value)}
              placeholder="e.g. ENTERPRISE"
              className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm" />
          </div>
          <button type="submit" disabled={submitting}
            className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground disabled:opacity-60">
            {submitting ? "Generating\u2026" : "Generate"}
          </button>
          {error && <p className="w-full text-sm text-status-critical">{error}</p>}
        </form>
      </CardContent>
    </Card>
  );
}
