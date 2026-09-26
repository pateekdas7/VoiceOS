// Navigation trees per ADR-005 §5.1 (Admin) and §5.2 (Client).
// Each entry's `href` is the route this scaffold creates; `module` cross-references the ADR section that specs its backend contract.

export type NavItem = {
  label: string;
  href: string;
  module: string;
};

export const ADMIN_NAV: NavItem[] = [
  { label: "Dashboard", href: "/dashboard", module: "ADR §6.1 / §12.5" },
  { label: "Clients", href: "/clients", module: "ADR §6.1" },
  { label: "Revenue", href: "/revenue", module: "ADR §6.16" },
  { label: "Billing", href: "/billing", module: "ADR §6.16" },
  { label: "Infrastructure", href: "/infrastructure", module: "ADR §12.1 / §12.3" },
  { label: "System Health", href: "/monitoring/system-health", module: "ADR §9 / §12.5" },
  { label: "Alerts Center", href: "/monitoring/alerts", module: "ADR-006 §4" },
  { label: "Incident Timeline", href: "/monitoring/incidents", module: "ADR-006 (derived)" },
  { label: "AI Insights", href: "/monitoring/ai-insights", module: "ADR-006 §3.2/10" },
  { label: "AI Reports", href: "/monitoring/ai-reports", module: "ADR-006 §6.1" },
  { label: "Capacity Planning", href: "/monitoring/capacity", module: "ADR-006 §3.4" },
  { label: "System X", href: "/system-x", module: "Sprint-028 (Autonomous Ops)" },
  { label: "Analytics", href: "/analytics", module: "ADR §6.9" },
  { label: "Users & Roles", href: "/users-roles", module: "ADR §6.8" },
  { label: "Audit Logs", href: "/audit-logs", module: "ADR §12.4" },
  { label: "Security", href: "/security", module: "ADR §12.4" },
  { label: "Compliance", href: "/compliance", module: "ADR §12.4" },
  { label: "Platform Settings", href: "/platform-settings", module: "ADR §6.17 / §6.18" },
];

// Superseded by the Campaign -> Pipeline hierarchy (frontend/components/client/client-nav-tree.tsx)
// for the primary sidebar -- kept as the flat route list these pages still live at
// (none were deleted; see CHANGELOG for the information-architecture rework).
export const CLIENT_NAV: NavItem[] = [
  { label: "Dashboard", href: "/dashboard", module: "ADR §6" },
  { label: "Campaigns", href: "/campaigns", module: "ADR §6.2 / §6.3" },
  { label: "Leads", href: "/leads", module: "ADR §6.4" },
  { label: "Live Calls", href: "/live-calls", module: "ADR §6.6 / §12.2" },
  { label: "Call History", href: "/call-history", module: "ADR §6.6 / §10" },
  { label: "Analytics", href: "/analytics", module: "ADR §6.9" },
  { label: "CRM", href: "/crm", module: "ADR §6.5" },
  { label: "Collections", href: "/collections", module: "ADR §6.7" },
  { label: "Reports", href: "/reports", module: "ADR §6.9" },
  { label: "Team Members", href: "/team", module: "ADR §6.8" },
  { label: "Settings", href: "/settings", module: "ADR §6.17 / §6.10 / §6.13 / §6.14" },
];

// The new top-level Client sidebar (product-workflow IA). "Campaigns" is
// rendered specially by ClientNavTree (expandable, backed by real campaign
// data) rather than as a plain link -- every other entry here is a plain leaf.
export type ClientTopNavItem = {
  label: string;
  href: string;
  /** RBAC permission gating this item, when one exists in the current fixed
   * tenant permission vocabulary (src/services/authz/roles.py). Omitted for
   * items with no natural existing permission -- see the RBAC-visibility
   * note in client-nav-tree.tsx for why this can't be fully enforced yet. */
  permission?: string;
};

export const CLIENT_TOP_NAV: ClientTopNavItem[] = [
  { label: "Dashboard", href: "/dashboard" },
  { label: "Campaigns", href: "/campaigns", permission: "write:campaigns" },
  { label: "Team", href: "/team", permission: "write:users" },
  { label: "Knowledge Base", href: "/knowledge-base" },
  { label: "Integrations", href: "/integrations" },
  { label: "Credentials", href: "/credentials" },
  { label: "General Settings", href: "/general-settings" },
  { label: "Audit Logs", href: "/audit-logs", permission: "read:audit" },
  { label: "Settings", href: "/settings" },
];
