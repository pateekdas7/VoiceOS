import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";

export default function CredentialsPage() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Credentials</h1>
          <p className="mt-1 text-xs text-muted">
            API keys for programmatic access to the VoiceOS platform APIs.
          </p>
        </div>
        <button
          disabled
          className="cursor-not-allowed rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground opacity-40"
          title="Requires tenant API-key issuance backend route"
        >
          Generate Key
        </button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>API Keys</CardTitle>
        </CardHeader>
        <Table>
          <TableHead>
            <TableRow>
              <TableHeaderCell>Name</TableHeaderCell>
              <TableHeaderCell>Key (masked)</TableHeaderCell>
              <TableHeaderCell>Created</TableHeaderCell>
              <TableHeaderCell>Last Used</TableHeaderCell>
              <TableHeaderCell>Status</TableHeaderCell>
              <TableHeaderCell>Actions</TableHeaderCell>
            </TableRow>
          </TableHead>
          <TableBody>
            <TableRow>
              <TableCell colSpan={6} className="py-12 text-center text-sm text-muted">
                No API keys yet. Generate a key to access VoiceOS APIs programmatically.
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Usage &amp; Limits</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-3 gap-4">
          {[
            { label: "Keys issued", value: "0 / 5" },
            { label: "Requests today", value: "—" },
            { label: "Rate limit", value: "300 / min" },
          ].map(({ label, value }) => (
            <div key={label} className="rounded-lg border border-border bg-background p-3">
              <p className="text-xs text-muted">{label}</p>
              <p className="mt-1 text-lg font-semibold tabular-nums">{value}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Security best practices</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 text-sm text-muted">
          <p>• Never commit API keys to source control. Use environment variables or a secrets manager.</p>
          <p>• Rotate keys every 90 days or immediately after a suspected exposure.</p>
          <p>• Use the minimum required scopes — read-only keys cannot modify campaigns or leads.</p>
          <p>• All API key activity is recorded in Audit Logs.</p>
        </CardContent>
      </Card>
    </div>
  );
}
