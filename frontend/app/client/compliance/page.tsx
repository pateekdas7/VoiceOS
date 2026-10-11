"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatTile } from "@/components/ui/card";

const BFF = process.env.NEXT_PUBLIC_BFF_URL || "/bff";

type ComplianceReport = {
  dnc_total: number;
  active_violations: number;
  resolved_violations: number;
  last_active_violation_at: string | null;
  audit_events_30d: number;
  out_of_hours_calls_30d: number;
  rbi_calling_hours: string;
  rbi_calling_hours_enforced: boolean;
  dpdp_compliant: boolean;
  compliance_score: number;
};

type Violation = {
  violation_id: string;
  rule_id: string;
  signal_summary: string;
  status: "ACTIVE" | "RESOLVED";
  detected_at: string;
  resolved_at: string | null;
  redetected_at: string | null;
};

function fmt(d: string) {
  return new Date(d).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export default function CompliancePage() {
  const [report, setReport] = useState<ComplianceReport | null>(null);
  const [violations, setViolations] = useState<Violation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resolving, setResolving] = useState<string | null>(null);
  const [resolveMsg, setResolveMsg] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    Promise.all([
      fetch(`${BFF}/compliance/report`, { credentials: "include" }).then((r) => r.json()),
      fetch(`${BFF}/compliance/violations`, { credentials: "include" }).then((r) => r.json()),
    ])
      .then(([rpt, viol]) => {
        if (rpt.error) throw new Error(rpt.error);
        setReport(rpt as ComplianceReport);
        setViolations(Array.isArray(viol) ? (viol as Violation[]) : []);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const handleResolve = async (vid: string) => {
    setResolving(vid);
    setResolveMsg(null);
    try {
      const r = await fetch(`${BFF}/compliance/violations/${vid}/resolve`, {
        method: "POST",
        credentials: "include",
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error ?? r.statusText);
      setResolveMsg("Violation resolved.");
      load();
    } catch (e: unknown) {
      setResolveMsg((e as Error).message);
    } finally {
      setResolving(null);
    }
  };

  const activeViolations = violations.filter((v) => v.status === "ACTIVE");
  const resolvedViolations = violations.filter((v) => v.status === "RESOLVED");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-sm font-semibold">Compliance</h1>
          <p className="mt-0.5 text-xs text-muted">RBI FPC, DPDP, TRAI NDNC, and internal policy status.</p>
        </div>
        <div className="flex gap-2">
          <Link
            href="/client/compliance/dnc"
            className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground hover:bg-muted/80"
          >
            Manage DNC
          </Link>
          <Link
            href="/client/audit-logs"
            className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground hover:bg-muted/80"
          >
            Audit Logs
          </Link>
        </div>
      </div>

      {loading && <p className="text-xs text-muted">Loading…</p>}
      {error && <p className="text-xs text-destructive">{error}</p>}
      {resolveMsg && <p className="text-xs text-muted">{resolveMsg}</p>}

      {report && (
        <>
          {/* Score + status */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile
              label="Compliance Score"
              value={`${report.compliance_score}/100`}
              hint="100 = no active violations"
            />
            <StatTile
              label="Active Violations"
              value={String(report.active_violations)}
              hint="Open policy breaches"
            />
            <StatTile
              label="DNC Registry"
              value={String(report.dnc_total)}
              hint="Numbers blocked from dialling"
            />
            <StatTile
              label="Audit Events (30d)"
              value={String(report.audit_events_30d)}
              hint="Immutable audit trail entries"
            />
          </div>

          {/* Policy status */}
          <Card>
            <CardHeader><CardTitle>Policy Enforcement Status</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatTile
                label="RBI Calling Hours"
                value={report.rbi_calling_hours_enforced ? "Enforced" : "⚠ Not Enforced"}
                hint={report.rbi_calling_hours}
              />
              <StatTile
                label="DPDP Compliant"
                value={report.dpdp_compliant ? "Yes" : "No — fix violations"}
                hint="Digital Personal Data Protection Act 2023"
              />
              <StatTile
                label="Out-of-hours Calls (30d)"
                value={String(report.out_of_hours_calls_30d)}
                hint={`Calls outside ${report.rbi_calling_hours}`}
              />
              <StatTile
                label="Resolved Violations"
                value={String(report.resolved_violations)}
                hint="Cleared in all time"
              />
            </CardContent>
          </Card>

          {/* Active violations */}
          {activeViolations.length > 0 && (
            <Card>
              <CardHeader><CardTitle>Active Violations</CardTitle></CardHeader>
              <CardContent className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b text-muted">
                      <th className="py-1 pr-3 text-left">Rule</th>
                      <th className="py-1 pr-3 text-left">Signal</th>
                      <th className="py-1 pr-3 text-left">Detected</th>
                      <th className="py-1 pr-3 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activeViolations.map((v) => (
                      <tr key={v.violation_id} className="border-b last:border-0 hover:bg-muted/20">
                        <td className="py-1 pr-3 font-mono text-xs">{v.rule_id}</td>
                        <td className="py-1 pr-3 max-w-[200px] truncate" title={v.signal_summary}>{v.signal_summary}</td>
                        <td className="py-1 pr-3 text-muted">{fmt(v.detected_at)}</td>
                        <td className="py-1 pr-3 text-right">
                          <button
                            onClick={() => handleResolve(v.violation_id)}
                            disabled={resolving === v.violation_id}
                            className="rounded bg-primary px-2 py-0.5 text-xs text-primary-foreground hover:opacity-90 disabled:opacity-50"
                          >
                            {resolving === v.violation_id ? "Resolving…" : "Resolve"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          )}

          {/* Resolved violations (collapsible) */}
          {resolvedViolations.length > 0 && (
            <Card>
              <CardHeader><CardTitle>Recently Resolved ({resolvedViolations.length})</CardTitle></CardHeader>
              <CardContent className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b text-muted">
                      <th className="py-1 pr-3 text-left">Rule</th>
                      <th className="py-1 pr-3 text-left">Signal</th>
                      <th className="py-1 pr-3 text-left">Resolved</th>
                    </tr>
                  </thead>
                  <tbody>
                    {resolvedViolations.slice(0, 10).map((v) => (
                      <tr key={v.violation_id} className="border-b last:border-0 hover:bg-muted/20 opacity-60">
                        <td className="py-1 pr-3 font-mono text-xs">{v.rule_id}</td>
                        <td className="py-1 pr-3 max-w-[200px] truncate" title={v.signal_summary}>{v.signal_summary}</td>
                        <td className="py-1 pr-3 text-muted">{v.resolved_at ? fmt(v.resolved_at) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          )}

          {report.active_violations === 0 && (
            <p className="text-xs text-muted">No active violations — platform is compliant.</p>
          )}
        </>
      )}
    </div>
  );
}
