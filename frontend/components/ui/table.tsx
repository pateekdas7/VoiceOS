export function Table({ children }: { children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-left text-sm">{children}</table>
    </div>
  );
}

export function TableHead({ children }: { children: React.ReactNode }) {
  return <thead className="bg-background text-xs font-medium uppercase text-muted">{children}</thead>;
}

export function TableBody({ children }: { children: React.ReactNode }) {
  return <tbody className="divide-y divide-border">{children}</tbody>;
}

export function TableRow({ children }: { children: React.ReactNode }) {
  return <tr className="hover:bg-background/60">{children}</tr>;
}

export function TableHeaderCell({ children }: { children: React.ReactNode }) {
  return <th className="px-4 py-2.5">{children}</th>;
}

export function TableCell({
  children,
  className = "",
  colSpan,
}: {
  children: React.ReactNode;
  className?: string;
  colSpan?: number;
}) {
  return (
    <td className={`px-4 py-2.5 ${className}`} colSpan={colSpan}>
      {children}
    </td>
  );
}
