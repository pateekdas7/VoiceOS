"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const BFF = process.env.NEXT_PUBLIC_BFF_URL || "/bff";

type DNCEntry = {
  dnc_id: string;
  phone: string;
  source: string;
  reason: string | null;
  added_at: string;
  expires_at: string | null;
  is_global: boolean;
};

type DNCList = { total: number; items: DNCEntry[] };

const SOURCE_LABELS: Record<string, string> = {
  manual: "Manual",
  ndnc: "NDNC",
  trai: "TRAI",
  tenant_upload: "Bulk Upload",
  opted_out: "Opted Out",
};

function fmt(d: string) {
  return new Date(d).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" });
}

export default function DNCPage() {
  const [data, setData] = useState<DNCList | null>(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Add form
  const [phone, setPhone] = useState("");
  const [reason, setReason] = useState("");
  const [source, setSource] = useState("manual");
  const [adding, setAdding] = useState(false);
  const [addMsg, setAddMsg] = useState<string | null>(null);

  const [removing, setRemoving] = useState<string | null>(null);

  const load = (q = "") => {
    setLoading(true);
    const url = `${BFF}/compliance/dnc?limit=200${q ? `&search=${encodeURIComponent(q)}` : ""}`;
    fetch(url, { credentials: "include" })
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error ?? r.statusText);
        return r.json() as Promise<DNCList>;
      })
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    load(search.trim());
  };

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    setAdding(true);
    setAddMsg(null);
    try {
      const r = await fetch(`${BFF}/compliance/dnc`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone: phone.trim(), reason: reason.trim() || undefined, source }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error ?? r.statusText);
      setAddMsg(`${phone.trim()} added to DNC.`);
      setPhone(""); setReason("");
      load(search);
    } catch (e: unknown) {
      setAddMsg((e as Error).message);
    } finally {
      setAdding(false);
    }
  };

  const handleRemove = async (phone: string) => {
    setRemoving(phone);
    try {
      const r = await fetch(`${BFF}/compliance/dnc/${encodeURIComponent(phone)}`, {
        method: "DELETE",
        credentials: "include",
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error ?? r.statusText);
      load(search);
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-sm font-semibold">DNC Registry</h1>
        <p className="mt-0.5 text-xs text-muted">
          Numbers blocked from dialling. Enforced before every call (TRAI NDNC / RBI FPC compliance).
        </p>
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}

      {/* Add number */}
      <Card>
        <CardHeader><CardTitle>Add to DNC</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={handleAdd} className="flex flex-wrap gap-2 items-end">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted">Phone (10-digit)</label>
              <input
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="9876543210"
                required
                className="rounded border border-border bg-background px-2 py-1 text-xs w-36"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted">Source</label>
              <select
                value={source}
                onChange={(e) => setSource(e.target.value)}
                className="rounded border border-border bg-background px-2 py-1 text-xs"
              >
                {Object.entries(SOURCE_LABELS).map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1 flex-1 min-w-[120px]">
              <label className="text-xs text-muted">Reason (optional)</label>
              <input
                type="text"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="e.g. customer opted out"
                className="rounded border border-border bg-background px-2 py-1 text-xs"
              />
            </div>
            <button
              type="submit"
              disabled={adding}
              className="rounded bg-primary px-3 py-1 text-xs text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              {adding ? "Adding…" : "Block Number"}
            </button>
          </form>
          {addMsg && <p className="mt-2 text-xs text-muted">{addMsg}</p>}
        </CardContent>
      </Card>

      {/* Search + list */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Blocked Numbers {data && `(${data.total})`}</CardTitle>
            <form onSubmit={handleSearch} className="flex gap-1">
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search phone…"
                className="rounded border border-border bg-background px-2 py-1 text-xs w-32"
              />
              <button
                type="submit"
                className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground hover:bg-muted/80"
              >
                Search
              </button>
            </form>
          </div>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          {loading ? (
            <p className="py-6 text-center text-xs text-muted">Loading…</p>
          ) : !data || data.items.length === 0 ? (
            <p className="py-6 text-center text-xs text-muted">No DNC numbers found.</p>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b text-muted">
                  <th className="py-1 pr-3 text-left">Phone</th>
                  <th className="py-1 pr-3 text-left">Source</th>
                  <th className="py-1 pr-3 text-left">Reason</th>
                  <th className="py-1 pr-3 text-left">Added</th>
                  <th className="py-1 pr-3 text-left">Expires</th>
                  <th className="py-1 pr-3 text-left">Scope</th>
                  <th className="py-1 pr-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((entry) => (
                  <tr key={entry.dnc_id} className="border-b last:border-0 hover:bg-muted/20">
                    <td className="py-1 pr-3 font-mono">{entry.phone}</td>
                    <td className="py-1 pr-3 capitalize">{SOURCE_LABELS[entry.source] ?? entry.source}</td>
                    <td className="py-1 pr-3 max-w-[140px] truncate text-muted" title={entry.reason ?? ""}>
                      {entry.reason || "—"}
                    </td>
                    <td className="py-1 pr-3 text-muted">{fmt(entry.added_at)}</td>
                    <td className="py-1 pr-3 text-muted">{entry.expires_at ? fmt(entry.expires_at) : "Never"}</td>
                    <td className="py-1 pr-3">
                      {entry.is_global ? (
                        <span className="rounded bg-destructive/20 px-1 text-destructive text-xs">Global</span>
                      ) : (
                        <span className="rounded bg-muted px-1 text-muted-foreground text-xs">Tenant</span>
                      )}
                    </td>
                    <td className="py-1 pr-3 text-right">
                      {!entry.is_global && (
                        <button
                          onClick={() => handleRemove(entry.phone)}
                          disabled={removing === entry.phone}
                          className="rounded bg-destructive/10 px-2 py-0.5 text-xs text-destructive hover:bg-destructive/20 disabled:opacity-50"
                        >
                          {removing === entry.phone ? "…" : "Remove"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
