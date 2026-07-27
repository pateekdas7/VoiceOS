import { SectionPlaceholder } from "@/components/client/section-placeholder";

export default function CredentialsPage() {
  return (
    <SectionPlaceholder
      title="Credentials"
      description="API keys and service-account credentials for this tenant. src/libs/secrets exists for platform-side secret management, but no tenant-facing API-key issuance BFF route exists yet."
      columns={["Name", "Created", "Last Used", "Actions"]}
      primaryAction="Generate Key"
      note="0 credentials"
    />
  );
}
