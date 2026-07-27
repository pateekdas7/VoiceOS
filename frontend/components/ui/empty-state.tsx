import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

// Used by every page whose backend contract is specified (ADR-005) but not yet wired.
// Deliberately honest about status — never fakes data.
export function EmptyState({
  title,
  description,
  adrRef,
  status = "not-wired",
}: {
  title: string;
  description: string;
  adrRef: string;
  status?: "not-wired" | "backend-pending";
}) {
  return (
    <Card>
      <CardContent className="flex flex-col items-start gap-3 py-10">
        <Badge tone="neutral">
          {status === "not-wired" ? "Not yet wired to backend" : "Backend pending"}
        </Badge>
        <h3 className="text-base font-semibold">{title}</h3>
        <p className="max-w-xl text-sm text-muted">{description}</p>
        <p className="text-xs text-muted">Spec: {adrRef}</p>
      </CardContent>
    </Card>
  );
}
