"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatTile } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";

// ─── Types ───────────────────────────────────────────────────────────────────

type Lead = {
  lead_id: string;
  campaign_id: string;
  pipeline_id: string | null;
  name: string;
  phone: string;
  email: string | null;
  language: string;
  score: number;
  status: string;
  queue_status: string;
  is_duplicate: boolean;
  is_blacklisted: boolean;
  rejection_reason: string | null;
  metadata: Record<string, string>;
  created_at: string;
};

type LeadImport = {
  import_id: string;
  filename: string;
  status: string;
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  duplicate_rows: number;
  created_at: string;
};

type Stats = {
  total: string;
  valid: string;
  rejected: string;
  duplicates: string;
  assigned: string;
  queued: string;
  avg_score: string | null;
};

type Pipeline = { pipeline_id: string; name: string };

// ─── Constants ────────────────────────────────────────────────────────────────

const STANDARD_FIELDS = [
  { value: "", label: "— skip —" },
  { value: "name", label: "Name" },
  { value: "phone", label: "Phone / Mobile" },
  { value: "email", label: "Email" },
  { value: "loan_amount", label: "Loan Amount" },
  { value: "city", label: "City" },
  { value: "state", label: "State" },
  { value: "product_type", label: "Product Type" },
  { value: "dpd", label: "DPD (Days Past Due)" },
  { value: "outstanding", label: "Outstanding Amount" },
];

const STATUS_TONE: Record<string, "neutral" | "healthy" | "warning" | "critical"> = {
  NEW: "neutral", VALIDATED: "neutral", QUALIFIED: "healthy",
  ASSIGNED: "healthy", CALLED: "warning", COMPLETED: "healthy",
  REJECTED: "critical", DUPLICATE: "neutral", BLACKLISTED: "critical",
};

const LANG_LABELS: Record<string, string> = {
  HINDI: "HI", TAMIL: "TA", TELUGU: "TE", MARATHI: "MR",
  GUJARATI: "GU", KANNADA: "KN", MALAYALAM: "ML", BENGALI: "BN",
  PUNJABI: "PA", ODIA: "OR",
};

// ─── CSV parser ───────────────────────────────────────────────────────────────

function parseCSV(text: string): { headers: string[]; rows: Record<string, string>[] } {
  const lines = text.trim().split(/\r?\n/);
  if (lines.length < 2) return { headers: [], rows: [] };
  const parseRow = (line: string) => {
    const cells: string[] = [];
    let cur = "", inQ = false;
    for (const ch of line) {
      if (ch === '"') { inQ = !inQ; }
      else if (ch === ',' && !inQ) { cells.push(cur.trim()); cur = ""; }
      else cur += ch;
    }
    cells.push(cur.trim());
    return cells;
  };
  const headers = parseRow(lines[0]);
  const rows = lines.slice(1).filter(l => l.trim()).map(line => {
    const vals = parseRow(line);
    return Object.fromEntries(headers.map((h, i) => [h, vals[i] ?? ""]));
  });
  return { headers, rows };
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function ScoreBadge({ score }: { score: number }) {
  const tone = score >= 80 ? "healthy" : score >= 60 ? "warning" : "neutral";
  return <Badge tone={tone}>{score}</Badge>;
}

function QueueStatusBadge({ qs }: { qs: string }) {
  const map: Record<string, "neutral" | "healthy" | "warning"> = {
    PENDING: "neutral", QUEUED: "warning", IN_CALL: "warning", DONE: "healthy",
  };
  return <Badge tone={map[qs] ?? "neutral"}>{qs}</Badge>;
}

// ─── Upload Wizard ────────────────────────────────────────────────────────────

type WizardStep = "select" | "map" | "result";

function UploadWizard({
  campaignId,
  pipelines,
  onDone,
}: {
  campaignId: string;
  pipelines: Pipeline[];
  onDone: () => void;
}) {
  const [step, setStep] = useState<WizardStep>("select");
  const [file, setFile] = useState<{ name: string; headers: string[]; rows: Record<string, string>[] } | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<{ total: number; valid: number; invalid: number; duplicates: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = async (ev) => {
      const text = ev.target?.result as string;
      const { headers, rows } = parseCSV(text);
      if (!headers.length) { setError("Could not parse CSV — check the file format."); return; }
      setFile({ name: f.name, headers, rows });
      // Auto-suggest mapping from BFF
      try {
        const res = await fetch(`/bff/campaigns/${campaignId}/leads/suggest-mapping`, {
          method: "POST", credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ columns: headers }),
        });
        const data = await res.json();
        setMapping(data.suggested_mapping ?? {});
      } catch { setMapping({}); }
      setStep("map");
    };
    reader.readAsText(f);
  }

  async function handleUpload() {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const pipelineIds = pipelines.map(p => p.pipeline_id);
      const res = await fetch(`/bff/campaigns/${campaignId}/leads/upload`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: file.name,
          columns: file.headers,
          column_mapping: mapping,
          rows: file.rows,
          pipeline_ids: pipelineIds,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Upload failed");
      setResult({ total: data.total, valid: data.valid, invalid: data.invalid, duplicates: data.duplicates });
      setStep("result");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  if (step === "select") {
    return (
      <Card>
        <CardHeader><CardTitle>Upload Leads</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-4">
          {error ? <p className="text-sm text-red-500">{error}</p> : null}
          <div
            className="flex flex-col items-center justify-center rounded-lg border-2 border-dashed border-border p-10 gap-3 cursor-pointer hover:border-brand/50 transition-colors"
            onClick={() => inputRef.current?.click()}
            onDragOver={e => e.preventDefault()}
            onDrop={e => {
              e.preventDefault();
              const f = e.dataTransfer.files[0];
              if (f) inputRef.current && Object.defineProperty(inputRef.current, 'files', { value: [f] });
              handleFileChange({ target: { files: e.dataTransfer.files } } as React.ChangeEvent<HTMLInputElement>);
            }}
          >
            <div className="text-4xl">📂</div>
            <p className="text-sm font-medium">Drop a CSV or Excel file here, or click to browse</p>
            <p className="text-xs text-muted">Supports .csv, .xlsx — max 10,000 rows</p>
            <button type="button" className="rounded-md bg-brand px-4 py-2 text-sm font-medium text-brand-foreground">
              Select File
            </button>
          </div>
          <input ref={inputRef} type="file" accept=".csv,.xlsx,.xls" className="hidden" onChange={handleFileChange} />
          <div className="rounded-lg border border-border bg-background p-4">
            <p className="text-xs font-semibold mb-2">Expected CSV format (example):</p>
            <pre className="text-xs text-muted overflow-x-auto">Name,Mobile No,Loan Amount,City,State{"\n"}Rahul Sharma,9876543210,500000,Delhi,Delhi{"\n"}Anita Verma,9988776655,200000,Mumbai,Maharashtra</pre>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (step === "map" && file) {
    const preview = file.rows.slice(0, 3);
    return (
      <Card>
        <CardHeader>
          <CardTitle>Map Columns — {file.name}</CardTitle>
          <p className="mt-1 text-xs text-muted">{file.rows.length} rows detected. Map your CSV columns to VoiceOS standard fields.</p>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          {error ? <p className="text-sm text-red-500">{error}</p> : null}
          <div className="grid grid-cols-2 gap-3">
            {file.headers.map(col => (
              <div key={col} className="flex items-center gap-2">
                <span className="w-40 truncate text-sm font-medium" title={col}>{col}</span>
                <span className="text-muted">→</span>
                <select
                  value={mapping[col] || ""}
                  onChange={e => setMapping(prev => ({ ...prev, [col]: e.target.value }))}
                  className="flex-1 rounded-md border border-border bg-background px-2 py-1.5 text-sm"
                >
                  {STANDARD_FIELDS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
                </select>
              </div>
            ))}
          </div>

          <div>
            <p className="mb-2 text-xs font-semibold text-muted">Preview (first 3 rows):</p>
            <div className="overflow-x-auto rounded border border-border text-xs">
              <table className="w-full">
                <thead className="bg-background">
                  <tr>{file.headers.map(h => <th key={h} className="px-3 py-2 text-left text-muted font-medium">{h}</th>)}</tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {preview.map((row, i) => (
                    <tr key={i}>{file.headers.map(h => <td key={h} className="px-3 py-2">{row[h]}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="flex gap-3">
            <button onClick={() => setStep("select")} className="rounded-md border border-border px-4 py-2 text-sm">Back</button>
            <button
              onClick={handleUpload}
              disabled={uploading || !Object.values(mapping).includes("phone")}
              className="rounded-md bg-brand px-4 py-2 text-sm font-medium text-brand-foreground disabled:opacity-50"
              title={!Object.values(mapping).includes("phone") ? "Map a phone/mobile column first" : ""}
            >
              {uploading ? "Processing…" : `Import ${file.rows.length} leads`}
            </button>
            {!Object.values(mapping).includes("phone") && (
              <p className="self-center text-xs text-red-500">Map a column to "Phone / Mobile" to continue.</p>
            )}
          </div>
        </CardContent>
      </Card>
    );
  }

  if (step === "result" && result) {
    return (
      <Card>
        <CardHeader><CardTitle>Import Complete</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="grid grid-cols-4 gap-3">
            <StatTile label="Total Rows" value={String(result.total)} />
            <StatTile label="Valid & Imported" value={String(result.valid)} hint="Stored in campaign leads" />
            <StatTile label="Invalid" value={String(result.invalid)} hint="Failed validation" />
            <StatTile label="Duplicates" value={String(result.duplicates)} hint="Phone already in campaign" />
          </div>

          <div className="rounded-lg border border-border bg-background p-4 text-sm">
            <p className="font-medium mb-1">Processing pipeline applied:</p>
            <div className="flex flex-wrap gap-2 text-xs">
              {["✓ Phone normalization", "✓ Name normalization", "✓ Phone validation",
                "✓ Deduplication", "✓ Lead scoring", "✓ Language detection",
                pipelines.length > 0 ? "✓ Pipeline distribution" : "⚠ No pipelines — create one to distribute"].map(s => (
                <span key={s} className="rounded-full bg-brand/10 px-2 py-0.5 text-brand">{s}</span>
              ))}
            </div>
          </div>

          <div className="flex gap-3">
            <button onClick={() => { setStep("select"); setFile(null); setResult(null); }} className="rounded-md border border-border px-4 py-2 text-sm">Import Another</button>
            <button onClick={onDone} className="rounded-md bg-brand px-4 py-2 text-sm font-medium text-brand-foreground">View Leads</button>
          </div>
        </CardContent>
      </Card>
    );
  }

  return null;
}

// ─── Main component ───────────────────────────────────────────────────────────

export function CampaignLeadsView({ campaignId, pipelines = [] }: { campaignId: string; pipelines?: Pipeline[] }) {
  const [activeTab, setActiveTab] = useState<"leads" | "upload" | "history">("leads");
  const [leads, setLeads] = useState<Lead[] | null>(null);
  const [imports, setImports] = useState<LeadImport[] | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [filterStatus, setFilterStatus] = useState("");
  const [filterPipeline, setFilterPipeline] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);

  const bff = process.env.NEXT_PUBLIC_BFF_URL || "/bff";

  const loadLeads = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filterStatus) params.set("status", filterStatus);
      if (filterPipeline) params.set("pipeline_id", filterPipeline);
      if (search) params.set("search", search);
      const [lr, sr, ir] = await Promise.allSettled([
        fetch(`${bff}/campaigns/${campaignId}/leads?${params}`, { credentials: "include" }).then(r => r.json()),
        fetch(`${bff}/campaigns/${campaignId}/leads/stats`, { credentials: "include" }).then(r => r.json()),
        fetch(`${bff}/campaigns/${campaignId}/leads/imports`, { credentials: "include" }).then(r => r.json()),
      ]);
      if (lr.status === "fulfilled") setLeads(Array.isArray(lr.value) ? lr.value : []);
      if (sr.status === "fulfilled") setStats(sr.value);
      if (ir.status === "fulfilled") setImports(Array.isArray(ir.value) ? ir.value : []);
    } finally {
      setLoading(false);
    }
  }, [campaignId, bff, filterStatus, filterPipeline, search]);

  useEffect(() => { loadLeads(); }, [loadLeads]);

  const pipelineLabel = (pid: string | null) => {
    if (!pid) return "—";
    const p = pipelines.find(p => p.pipeline_id === pid);
    return p ? p.name : pid.slice(0, 8) + "…";
  };

  const tabs: { id: "leads" | "upload" | "history"; label: string }[] = [
    { id: "leads", label: `Leads ${stats ? `(${stats.total})` : ""}` },
    { id: "upload", label: "Upload" },
    { id: "history", label: `Import History ${imports ? `(${imports.length})` : ""}` },
  ];

  return (
    <div className="flex flex-col gap-4">
      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
          <StatTile label="Total" value={stats.total ?? "0"} />
          <StatTile label="Valid" value={stats.valid ?? "0"} />
          <StatTile label="Rejected" value={stats.rejected ?? "0"} />
          <StatTile label="Duplicates" value={stats.duplicates ?? "0"} />
          <StatTile label="Assigned" value={stats.assigned ?? "0"} hint="To a pipeline" />
          <StatTile label="Avg Score" value={stats.avg_score ? `${stats.avg_score}` : "—"} />
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border">
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={`border-b-2 px-4 py-2 text-sm transition-colors ${
              activeTab === t.id
                ? "border-brand font-medium text-foreground"
                : "border-transparent text-muted hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Upload tab */}
      {activeTab === "upload" && (
        <UploadWizard
          campaignId={campaignId}
          pipelines={pipelines}
          onDone={() => { setActiveTab("leads"); loadLeads(); }}
        />
      )}

      {/* Import history tab */}
      {activeTab === "history" && (
        <Card>
          <CardHeader><CardTitle>Import History</CardTitle></CardHeader>
          {imports === null ? (
            <CardContent className="py-10 text-center text-sm text-muted">Loading…</CardContent>
          ) : imports.length === 0 ? (
            <CardContent className="py-10 text-center text-sm text-muted">No imports yet.</CardContent>
          ) : (
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>File</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Total</TableHeaderCell>
                  <TableHeaderCell>Valid</TableHeaderCell>
                  <TableHeaderCell>Invalid</TableHeaderCell>
                  <TableHeaderCell>Duplicates</TableHeaderCell>
                  <TableHeaderCell>Date</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {imports.map(imp => (
                  <TableRow key={imp.import_id}>
                    <TableCell className="font-medium">{imp.filename}</TableCell>
                    <TableCell><Badge tone={imp.status === "DONE" ? "healthy" : "neutral"}>{imp.status}</Badge></TableCell>
                    <TableCell>{imp.total_rows}</TableCell>
                    <TableCell className="text-green-600">{imp.valid_rows}</TableCell>
                    <TableCell className="text-red-500">{imp.invalid_rows}</TableCell>
                    <TableCell className="text-muted">{imp.duplicate_rows}</TableCell>
                    <TableCell className="text-muted">{new Date(imp.created_at).toLocaleDateString()}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Card>
      )}

      {/* Leads tab */}
      {activeTab === "leads" && (
        <div className="flex flex-col gap-3">
          {/* Filters */}
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="search"
              placeholder="Search name or phone…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="rounded-md border border-border bg-background px-3 py-1.5 text-sm w-52"
            />
            <select
              value={filterStatus}
              onChange={e => setFilterStatus(e.target.value)}
              className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            >
              <option value="">All Statuses</option>
              {["NEW", "VALIDATED", "QUALIFIED", "ASSIGNED", "CALLED", "COMPLETED", "REJECTED"].map(s => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            <select
              value={filterPipeline}
              onChange={e => setFilterPipeline(e.target.value)}
              className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            >
              <option value="">All Pipelines</option>
              <option value="unassigned">Unassigned</option>
              {pipelines.map(p => <option key={p.pipeline_id} value={p.pipeline_id}>{p.name}</option>)}
            </select>
            <button
              onClick={loadLeads}
              className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-background"
            >
              Refresh
            </button>
            <button
              onClick={() => setActiveTab("upload")}
              className="ml-auto rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground"
            >
              Upload Leads
            </button>
          </div>

          <Card>
            {loading ? (
              <CardContent className="py-10 text-center text-sm text-muted">Loading leads…</CardContent>
            ) : !leads || leads.length === 0 ? (
              <CardContent className="flex flex-col items-center gap-3 py-14 text-center">
                <p className="text-sm font-medium">No leads yet</p>
                <p className="text-xs text-muted">Upload a CSV file to start the lead intake pipeline.</p>
                <button
                  onClick={() => setActiveTab("upload")}
                  className="rounded-md bg-brand px-4 py-2 text-sm font-medium text-brand-foreground"
                >
                  Upload Leads
                </button>
              </CardContent>
            ) : (
              <Table>
                <TableHead>
                  <TableRow>
                    <TableHeaderCell>Name</TableHeaderCell>
                    <TableHeaderCell>Phone</TableHeaderCell>
                    <TableHeaderCell>Language</TableHeaderCell>
                    <TableHeaderCell>Score</TableHeaderCell>
                    <TableHeaderCell>Status</TableHeaderCell>
                    <TableHeaderCell>Enrichment</TableHeaderCell>
                    <TableHeaderCell>Pipeline</TableHeaderCell>
                    <TableHeaderCell>Queue</TableHeaderCell>
                    <TableHeaderCell>Added</TableHeaderCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {leads.map(l => (
                    <TableRow key={l.lead_id}>
                      <TableCell className="font-medium">{l.name || "—"}</TableCell>
                      <TableCell className="font-mono text-sm">{l.phone}</TableCell>
                      <TableCell>
                        <span className="rounded bg-background border border-border px-1.5 py-0.5 text-xs font-mono">
                          {LANG_LABELS[l.language] || l.language}
                        </span>
                      </TableCell>
                      <TableCell><ScoreBadge score={l.score} /></TableCell>
                      <TableCell>
                        <Badge tone={STATUS_TONE[l.status] ?? "neutral"}>{l.status}</Badge>
                      </TableCell>
                      <TableCell>
                        {l.metadata && Object.keys(l.metadata).some(k => k.startsWith("crm_")) ? (
                          <Badge tone="healthy">enriched</Badge>
                        ) : (
                          <span className="text-xs text-muted">—</span>
                        )}
                      </TableCell>
                      <TableCell className="text-sm text-muted">{pipelineLabel(l.pipeline_id)}</TableCell>
                      <TableCell><QueueStatusBadge qs={l.queue_status} /></TableCell>
                      <TableCell className="text-muted">{new Date(l.created_at).toLocaleDateString()}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
