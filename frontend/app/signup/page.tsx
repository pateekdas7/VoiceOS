import { AcceptInvitationForm } from "@/components/auth/accept-invitation-form";

export default async function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string; email?: string }>;
}) {
  const params = await searchParams;

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="flex w-full max-w-sm flex-col items-center gap-6">
        <div className="text-center">
          <p className="text-lg font-semibold">VoiceOS</p>
          <p className="text-sm text-muted">Accept your workspace invitation</p>
        </div>
        <AcceptInvitationForm token={params.token ?? null} email={params.email ?? null} />
      </div>
    </div>
  );
}
