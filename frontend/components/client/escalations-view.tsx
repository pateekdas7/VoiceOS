"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import {
  ApiError,
  claimNextHITLItem,
  listHITLQueue,
  recordHITLDecision,
  type HITLItem,
  type HITLPriority,
} from "@/lib/api/hitl";

const PRIORITY_TONE: Record<HITLPriority, "neutral" | "healthy" | "warning" | "critical"> = {
  CRITICAL: "critical",
  HIGH: "warning",
  MEDIUM: "neutral",
};

const DECISIONS = ["APPROVE", "REJECT", "OVERRIDE", "ESCALATE_FURTHER"] as const;

export function EscalationsView() {
  const [items, setItems] = useState<HITLItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [claimError, setClaimError] = useState<string | null>(null);
  const [claiming, setClaiming] = useState(false);
  const [decidingId, setDecidingId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listHITLQueue()
      .then((data) => {
        if (cancelled) return;
        setItems(data);
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
      setItems(await listHITLQueue());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
    }
  }

  async function handleClaimNext() {
    setClaimError(null);
    setClaiming(true);
    try {
      const claimed = await claimNextHITLItem();
      if (claimed === null) {
        setClaimError("No pending escalations to claim.");
      } else {
        await refresh();
      }
    } catch (err) {
      setClaimError(err instanceof ApiError ? err.message : "Could not claim next item.");
    } finally {
      setClaiming(false);
    }
  }

  async function handleDecision(item: HITLItem, decision: string, rationale: string) {
    if (!rationale.trim()) {
      setClaimError("A rationale is required to record a decision.");
      return;
    }
    setClaimError(null);
    setDecidingId(item.hitl_item_id);
    try {
      await recordHITLDecision(item.hitl_item_id, decision, rationale);
      await refresh();
    } catch (err) {
      setClaimError(err instanceof ApiError ? err.message : "Could not record decision.");
    } finally {
      setDecidingId(null);
    }
  }

  if (error) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
      </Card>
    );
  }

  if (items === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading escalation queue…</CardContent>
      </Card>
    );
  }

  const pending = items.filter((i) => i.status === "PENDING");
  const claimed = items.filter((i) => i.status === "CLAIMED");

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Escalation Queue</h2>
        <button
          type="button"
          disabled={claiming}
          onClick={handleClaimNext}
          className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground disabled:opacity-60"
        >
          {claiming ? "Claiming…" : "Claim Next"}
        </button>
      </div>

      {claimError ? <p className="text-sm text-status-critical">{claimError}</p> : null}

      <Card>
        <CardHeader>
          <CardTitle>
            {pending.length} pending, {claimed.length} claimed by you
          </CardTitle>
        </CardHeader>
        {items.length === 0 ? (
          <CardContent className="py-10 text-center text-sm text-muted">
            No items in the escalation queue.
          </CardContent>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Call</TableHeaderCell>
                <TableHeaderCell>Reason</TableHeaderCell>
                <TableHeaderCell>Priority</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>SLA Deadline</TableHeaderCell>
                <TableHeaderCell>Decision</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {items.map((item) => (
                <EscalationRow
                  key={item.hitl_item_id}
                  item={item}
                  busy={decidingId === item.hitl_item_id}
                  onDecide={handleDecision}
                />
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  );
}

function EscalationRow({
  item,
  busy,
  onDecide,
}: {
  item: HITLItem;
  busy: boolean;
  onDecide: (item: HITLItem, decision: string, rationale: string) => void;
}) {
  const [decision, setDecision] = useState<string>(DECISIONS[0]);
  const [rationale, setRationale] = useState("");

  return (
    <TableRow>
      <TableCell className="font-medium">{item.call_id}</TableCell>
      <TableCell className="max-w-xs text-muted">{item.reason}</TableCell>
      <TableCell>
        <Badge tone={PRIORITY_TONE[item.priority]}>{item.priority}</Badge>
        {item.sla_breached ? <Badge tone="critical">SLA breached</Badge> : null}
      </TableCell>
      <TableCell className="text-muted">{item.status}</TableCell>
      <TableCell className="text-muted">{new Date(item.sla_deadline_at).toLocaleString()}</TableCell>
      <TableCell>
        {item.status === "RESOLVED" ? (
          <span className="text-muted">—</span>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={decision}
              onChange={(e) => setDecision(e.target.value)}
              className="rounded-md border border-border bg-surface px-2 py-1 text-xs"
            >
              {DECISIONS.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
            <input
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              placeholder="Rationale (required)"
              className="min-w-[12rem] rounded-md border border-border bg-surface px-2 py-1 text-xs"
            />
            <button
              type="button"
              disabled={busy}
              onClick={() => onDecide(item, decision, rationale)}
              className="text-sm text-brand underline disabled:opacity-50"
            >
              {busy ? "Saving…" : "Submit"}
            </button>
          </div>
        )}
      </TableCell>
    </TableRow>
  );
}
