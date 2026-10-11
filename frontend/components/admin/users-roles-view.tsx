"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { ApiError, createPlatformUser, deactivatePlatformUser, listPlatformUsers, type PlatformUser } from "@/lib/api/admin-ops";

const ROLES = ["PLATFORM_ADMIN", "PLATFORM_SUPPORT", "PLATFORM_BILLING_OPS"];

export function UsersRolesView() {
  const [users, setUsers] = useState<PlatformUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function refresh() {
    try {
      setUsers(await listPlatformUsers());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
    }
  }

  useEffect(() => {
    let cancelled = false;
    listPlatformUsers()
      .then((data) => {
        if (cancelled) return;
        setUsers(data);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleDeactivate(id: string) {
    setBusyId(id);
    try {
      await deactivatePlatformUser(id);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not deactivate user.");
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
  if (users === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading platform users…</CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Users & Roles</h1>
        <button
          type="button"
          onClick={() => setShowForm((v) => !v)}
          className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground"
        >
          {showForm ? "Cancel" : "New Platform User"}
        </button>
      </div>

      {showForm ? (
        <CreatePlatformUserForm
          onCreated={() => {
            setShowForm(false);
            refresh();
          }}
        />
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>{users.length} platform user{users.length === 1 ? "" : "s"}</CardTitle>
        </CardHeader>
        <Table>
          <TableHead>
            <TableRow>
              <TableHeaderCell>Name</TableHeaderCell>
              <TableHeaderCell>Email</TableHeaderCell>
              <TableHeaderCell>Role</TableHeaderCell>
              <TableHeaderCell>Status</TableHeaderCell>
              <TableHeaderCell>Actions</TableHeaderCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {users.map((u) => (
              <TableRow key={u.platform_user_id}>
                <TableCell className="font-medium">{u.name}</TableCell>
                <TableCell className="text-muted">{u.email}</TableCell>
                <TableCell>
                  <Badge tone="brand">{u.platform_role}</Badge>
                </TableCell>
                <TableCell>
                  <Badge tone={u.is_active ? "healthy" : "critical"}>{u.is_active ? "Active" : "Inactive"}</Badge>
                </TableCell>
                <TableCell>
                  {u.is_active ? (
                    <button
                      type="button"
                      disabled={busyId === u.platform_user_id}
                      onClick={() => handleDeactivate(u.platform_user_id)}
                      className="text-sm text-brand underline disabled:opacity-50"
                    >
                      Deactivate
                    </button>
                  ) : (
                    <span className="text-sm text-muted">—</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}

function CreatePlatformUserForm({ onCreated }: { onCreated: () => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState(ROLES[1]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await createPlatformUser({ email, name, platform_role: role });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create platform user.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardContent>
        <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted" htmlFor="name">Name</label>
            <input
              id="name" required value={name} onChange={(e) => setName(e.target.value)}
              className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted" htmlFor="email">Email</label>
            <input
              id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
              className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted" htmlFor="role">Role</label>
            <select
              id="role" value={role} onChange={(e) => setRole(e.target.value)}
              className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
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
