import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";

// A structured placeholder for a section whose backend isn't wired yet --
// deliberately richer than EmptyState: it renders the REAL table/action shape
// this section will have once connected, with an honest "not yet connected"
// body instead of fabricated rows. Ready to be wired by swapping the empty
// `rows` prop for a real fetch, with no layout restructuring required.
export function SectionPlaceholder({
  title,
  description,
  columns,
  primaryAction,
  note,
}: {
  title: string;
  description: string;
  columns: string[];
  primaryAction?: string;
  note: string;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">{title}</h1>
          <p className="mt-1 text-xs text-muted">{description}</p>
        </div>
        {primaryAction ? (
          <button
            type="button"
            disabled
            title="Not yet connected to a backend"
            className="cursor-not-allowed rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground opacity-40"
          >
            {primaryAction}
          </button>
        ) : null}
      </div>
      <Card>
        <CardHeader>
          <CardTitle>{note}</CardTitle>
        </CardHeader>
        {columns.length > 0 ? (
          <Table>
            <TableHead>
              <TableRow>
                {columns.map((c) => (
                  <TableHeaderCell key={c}>{c}</TableHeaderCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              <TableRow>
                <TableCell colSpan={columns.length} className="py-10 text-center text-sm text-muted">
                  Not yet connected to a backend.
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        ) : (
          <CardContent className="py-10 text-center text-sm text-muted">Not yet connected to a backend.</CardContent>
        )}
      </Card>
    </div>
  );
}
