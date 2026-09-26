"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, listFeatureFlags, setFeatureFlag, type FeatureFlagCatalogEntry } from "@/lib/api/admin-ops";

const STATES = ["DISABLED", "ENABLED", "GRADUAL_ROLLOUT"];

export function PlatformSettingsView() {
  const [flags, setFlags] = useState<FeatureFlagCatalogEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function refresh() {
    try {
      setFlags(await listFeatureFlags());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
    }
  }

  useEffect(() => {
    let cancelled = false;
    listFeatureFlags()
      .then((data) => {
        if (cancelled) return;
        setFlags(data);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Could not reach the backend.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSetState(flagName: string, state: string) {
    setBusy(flagName);
    try {
      await setFeatureFlag(flagName, { scope: "GLOBAL", state });
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update flag.");
    } finally {
      setBusy(null);
    }
  }

  if (error) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-status-critical">{error}</CardContent>
      </Card>
    );
  }
  if (flags === null) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted">Loading feature flags…</CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">Platform Settings</h1>
      <p className="text-xs text-muted">
        Feature-flag targeting (V5 Ch23). This catalog lists the known flag vocabulary defined so far in this
        codebase -- there is no dynamic flag-registry table yet, so new flags require a code change to appear here.
      </p>
      {flags.map((flag) => {
        const globalRow = flag.targeting_rows.find((r) => r.scope === "GLOBAL");
        return (
          <Card key={flag.flag_name}>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span>{flag.flag_name}</span>
                <Badge tone={globalRow?.state === "ENABLED" ? "healthy" : "neutral"}>
                  {globalRow?.state ?? "DISABLED"}
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="flex items-end gap-3">
              <select
                disabled={busy === flag.flag_name}
                defaultValue={globalRow?.state ?? "DISABLED"}
                onChange={(e) => handleSetState(flag.flag_name, e.target.value)}
                className="rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm"
              >
                {STATES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <span className="text-xs text-muted">Sets the GLOBAL targeting row.</span>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
