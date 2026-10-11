"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { ApiError, createCustomer, listCustomers, type Customer } from "@/lib/api/client-ops";

export function CRMView() {
  const [customers, setCustomers] = useState<Customer[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listCustomers()
      .then((data) => {
        if (cancelled) return;
        setCustomers(data);
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
      setCustomers(await listCustomers());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
    }
  }

  if (error) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
      </Card>
    );
  }
  if (customers === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading customers…</CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">CRM</h1>
        <button
          type="button"
          onClick={() => setShowForm((v) => !v)}
          className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground"
        >
          {showForm ? "Cancel" : "New Customer"}
        </button>
      </div>

      {showForm ? (
        <CreateCustomerForm
          onCreated={() => {
            setShowForm(false);
            refresh();
          }}
        />
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>{customers.length} customer{customers.length === 1 ? "" : "s"}</CardTitle>
        </CardHeader>
        {customers.length === 0 ? (
          <CardContent className="py-10 text-center text-sm text-muted">No customers yet.</CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Name</TableHeaderCell>
                <TableHeaderCell>CRM ID</TableHeaderCell>
                <TableHeaderCell>Contact</TableHeaderCell>
                <TableHeaderCell>Language</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {customers.map((c) => (
                <TableRow key={c.customer_id}>
                  <TableCell className="font-medium">{c.name}</TableCell>
                  <TableCell className="text-muted">{c.crm_id}</TableCell>
                  <TableCell className="text-muted">
                    {c.contacts.find((ct) => ct.is_primary)?.value ?? c.contacts[0]?.value ?? "—"}
                  </TableCell>
                  <TableCell className="text-muted">{c.preferred_language}</TableCell>
                  <TableCell>
                    <Badge tone={c.is_active ? "healthy" : "critical"}>{c.is_active ? "Active" : "Inactive"}</Badge>
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

function CreateCustomerForm({ onCreated }: { onCreated: () => void }) {
  const [crmId, setCrmId] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await createCustomer({
        crm_id: crmId,
        name,
        contacts: phone ? [{ contact_type: "MOBILE", value: phone, is_primary: true }] : undefined,
      });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create customer.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardContent>
        <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted" htmlFor="crm_id">CRM ID</label>
            <input
              id="crm_id" required value={crmId} onChange={(e) => setCrmId(e.target.value)}
              className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted" htmlFor="name">Name</label>
            <input
              id="name" required value={name} onChange={(e) => setName(e.target.value)}
              className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted" htmlFor="phone">Phone (optional)</label>
            <input
              id="phone" value={phone} onChange={(e) => setPhone(e.target.value)}
              className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
              placeholder="+91XXXXXXXXXX"
            />
          </div>
          <button
            type="submit" disabled={submitting}
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
