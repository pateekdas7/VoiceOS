"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import {
  ApiError,
  deactivateTeamMember,
  inviteTeamMember,
  listRoles,
  listTeam,
  type TeamMember,
  type TeamRole,
} from "@/lib/api/team";

export function TeamView() {
  const [members, setMembers] = useState<TeamMember[] | null>(null);
  const [roles, setRoles] = useState<TeamRole[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showInviteForm, setShowInviteForm] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function refresh() {
    try {
      const [teamData, roleData] = await Promise.all([listTeam(), listRoles()]);
      setMembers(teamData);
      setRoles(roleData);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
    }
  }

  useEffect(() => {
    let cancelled = false;
    Promise.all([listTeam(), listRoles()])
      .then(([teamData, roleData]) => {
        if (cancelled) return;
        setMembers(teamData);
        setRoles(roleData);
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

  async function handleDeactivate(userId: string) {
    setActionError(null);
    setBusyId(userId);
    try {
      await deactivateTeamMember(userId);
      await refresh();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Action failed.");
    } finally {
      setBusyId(null);
    }
  }

  function roleName(roleId: string): string {
    return roles.find((r) => r.role_id === roleId)?.name ?? roleId;
  }

  if (error) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
      </Card>
    );
  }

  if (members === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading team…</CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Team Members</h1>
        <button
          type="button"
          onClick={() => setShowInviteForm((v) => !v)}
          className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground"
        >
          {showInviteForm ? "Cancel" : "Invite"}
        </button>
      </div>

      {showInviteForm ? (
        <InviteForm
          roles={roles}
          onInvited={() => {
            setShowInviteForm(false);
            refresh();
          }}
        />
      ) : null}

      {actionError ? <p className="text-sm text-status-critical">{actionError}</p> : null}

      <Card>
        <CardHeader>
          <CardTitle>
            {members.length} member{members.length === 1 ? "" : "s"}
          </CardTitle>
        </CardHeader>
        {members.length === 0 ? (
          <CardContent className="py-10 text-center text-sm text-muted">No team members yet.</CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Name</TableHeaderCell>
                <TableHeaderCell>Email</TableHeaderCell>
                <TableHeaderCell>Roles</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Actions</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {members.map((member) => (
                <TableRow key={member.user_id}>
                  <TableCell className="font-medium">{member.name}</TableCell>
                  <TableCell className="text-muted">{member.email}</TableCell>
                  <TableCell>
                    {member.roles.map((r) => (
                      <Badge key={r.role_id} tone="neutral">
                        {roleName(r.role_id)}
                      </Badge>
                    ))}
                  </TableCell>
                  <TableCell>
                    <Badge tone={member.is_active ? "healthy" : "critical"}>
                      {member.is_active ? "Active" : "Deactivated"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {member.is_active ? (
                      <button
                        type="button"
                        disabled={busyId === member.user_id}
                        onClick={() => handleDeactivate(member.user_id)}
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
        )}
      </Card>
    </div>
  );
}

function InviteForm({ roles, onInvited }: { roles: TeamRole[]; onInvited: () => void }) {
  const [email, setEmail] = useState("");
  const [roleId, setRoleId] = useState(roles[0]?.role_id ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await inviteTeamMember({ email, role_id: roleId });
      onInvited();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not send invitation.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardContent>
        <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
              placeholder="colleague@company.com"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted" htmlFor="role">
              Role
            </label>
            <select
              id="role"
              value={roleId}
              onChange={(e) => setRoleId(e.target.value)}
              className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
            >
              {roles.map((r) => (
                <option key={r.role_id} value={r.role_id}>
                  {r.name}
                </option>
              ))}
            </select>
          </div>
          <button
            type="submit"
            disabled={submitting || !roleId}
            className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground disabled:opacity-60"
          >
            {submitting ? "Sending…" : "Send Invite"}
          </button>
          {error ? <p className="w-full text-sm text-status-critical">{error}</p> : null}
        </form>
      </CardContent>
    </Card>
  );
}
