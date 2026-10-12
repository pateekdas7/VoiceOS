import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";

const CATEGORIES = ["All", "Guidelines", "FAQs", "Scripts", "Compliance", "Product"];

export default function KnowledgeBasePage() {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Knowledge Base</h1>
          <p className="mt-1 text-xs text-muted">
            Documents fed to the LLM as evidence-only context — the AI never invents facts outside these sources.
          </p>
        </div>
        <button
          disabled
          className="cursor-not-allowed rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-foreground opacity-40"
          title="Upload requires knowledge-base backend service"
        >
          Upload Document
        </button>
      </div>

      <div className="flex gap-2">
        {CATEGORIES.map((cat) => (
          <button
            key={cat}
            disabled
            className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
              cat === "All"
                ? "bg-brand/10 text-brand"
                : "border border-border text-muted opacity-50"
            }`}
          >
            {cat}
          </button>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Documents</CardTitle>
        </CardHeader>
        <Table>
          <TableHead>
            <TableRow>
              <TableHeaderCell>Document</TableHeaderCell>
              <TableHeaderCell>Category</TableHeaderCell>
              <TableHeaderCell>Size</TableHeaderCell>
              <TableHeaderCell>Updated</TableHeaderCell>
              <TableHeaderCell>Status</TableHeaderCell>
            </TableRow>
          </TableHead>
          <TableBody>
            <TableRow>
              <TableCell colSpan={5} className="py-12 text-center text-sm text-muted">
                No documents uploaded yet. Upload PDFs, DOCX, or TXT files to build the AI context library.
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>How it works</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {[
            { step: "1", title: "Upload documents", desc: "Add PDFs, DOC, or plain text files — RBI guidelines, product FAQs, compliance policies." },
            { step: "2", title: "Automatic indexing", desc: "Documents are chunked and embedded. The retrieval system finds relevant passages in real time during calls." },
            { step: "3", title: "Evidence-only grounding", desc: "The LLM cites only what's in your documents. It never fabricates facts or goes outside the provided context." },
          ].map(({ step, title, desc }) => (
            <div key={step} className="flex gap-3">
              <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand/10 text-xs font-semibold text-brand">
                {step}
              </div>
              <div>
                <p className="text-sm font-medium">{title}</p>
                <p className="text-xs text-muted">{desc}</p>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
