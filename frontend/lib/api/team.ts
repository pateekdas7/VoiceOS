// Typed client for the Client -> Team Members BFF routes (ADR-005 §6.8).

import { ApiError, bffGet, bffPost, bffDel } from "@/lib/api/fetch-client";
export { ApiError };

export type TeamMember = {
  user_id: string;
  email: string;
  name: string;
  is_active: boolean;
  roles: { role_id: string; scope_type: string; scope_id: string }[];
  created_at: string;
};

export type TeamRole = { role_id: string; name: string; permissions: string[] };

export const listTeam = () => bffGet<TeamMember[]>("/team");

export const listRoles = () => bffGet<TeamRole[]>("/team/roles");

export const inviteTeamMember = (input: { email: string; role_id: string }) =>
  bffPost<unknown>("/team/invite", input);

export const deactivateTeamMember = (userId: string) =>
  bffDel<TeamMember>(`/team/${encodeURIComponent(userId)}`);
