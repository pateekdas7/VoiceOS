import { StatTile } from "@/components/ui/card";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function PipelineAnalyticsPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-sm font-semibold">Pipeline Analytics</h1>
        <p className="mt-0.5 text-xs text-muted">Per-pipeline performance metrics. Requires pipeline execution backend (sprint pending).</p>
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatTile label="Leads Processed" value="—" hint="Total leads attempted" />
        <StatTile label="Contact Rate" value="—" hint="% leads contacted" />
        <StatTile label="PTP Rate" value="—" hint="Promise to pay rate" />
        <StatTile label="Avg Call Duration" value="—" hint="Seconds" />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <StatTile label="Successful Calls" value="—" hint="Connected + completed" />
        <StatTile label="No Answer" value="—" hint="Unanswered attempts" />
        <StatTile label="Busy / Failed" value="—" hint="Failed connection attempts" />
        <StatTile label="DND Blocked" value="—" hint="Do-not-disturb filtered" />
      </div>
      <Card>
        <CardHeader><CardTitle>Daily Call Volume</CardTitle></CardHeader>
        <CardContent className="flex h-48 items-center justify-center text-sm text-muted">
          Chart will render here once pipeline execution data is available.
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Outcome Breakdown</CardTitle></CardHeader>
        <CardContent className="flex h-32 items-center justify-center text-sm text-muted">
          Disposition distribution chart — pending pipeline backend.
        </CardContent>
      </Card>
    </div>
  );
}
