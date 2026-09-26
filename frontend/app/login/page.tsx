import { LoginForm } from "@/components/auth/login-form";

export default function LoginPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="flex w-full max-w-sm flex-col items-center gap-6">
        <div className="text-center">
          <p className="text-lg font-semibold">VoiceOS</p>
          <p className="text-sm text-muted">Sign in to continue</p>
        </div>
        <LoginForm />
        <p className="text-xs text-muted">
          Invited to a workspace?{" "}
          <a href="/signup" className="text-brand underline">
            Accept your invitation
          </a>
        </p>
      </div>
    </div>
  );
}
