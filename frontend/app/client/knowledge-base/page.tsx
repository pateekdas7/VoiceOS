import { SectionPlaceholder } from "@/components/client/section-placeholder";

export default function KnowledgeBasePage() {
  return (
    <SectionPlaceholder
      title="Knowledge Base"
      description="RBI guidelines/FAQs feeding the LLM as evidence-only context (ADR-005 §6.18, Law of Authority: the LLM never invents facts). No backend module exists for this yet."
      columns={["Document", "Category", "Updated"]}
      primaryAction="Upload Document"
      note="0 documents"
    />
  );
}
