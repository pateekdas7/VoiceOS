"use client";

import { useState } from "react";

// Tenant users are invited (admin_portal.user_admin.invite / user_management),
// not self-registered — this links an invitation to an IdP identity rather
// than creating a new, unauthorized tenant. No password field: VoiceOS has
// no first-party password storage anywhere (IdP-federated only, by design).
export function AcceptInvitationForm({ token, email }: { token: string | null; email: string | null }) {
  const [error, setError] = useState<string | null>(null);
  const bffUrl = process.env.NEXT_PUBLIC_BFF_URL;

  if (!token) {
    return (
      <p className="max-w-sm text-center text-sm text-muted">
        This link is missing an invitation token. Ask your workspace admin to resend the invite from
        Team Members.
      </p>
    );
  }

  const invitationToken = token;

  function handleGoogleSignUp() {
    if (!bffUrl) {
      setError("Backend not configured (NEXT_PUBLIC_BFF_URL is unset) — cannot accept invitation yet.");
      return;
    }
    window.location.href = `${bffUrl}/auth/google/start?invitation_token=${encodeURIComponent(invitationToken)}`;
  }

  return (
    <div className="flex w-full max-w-sm flex-col gap-4">
      {email ? <p className="text-center text-sm text-muted">Invited as {email}</p> : null}
      {error ? <p className="text-sm text-status-critical">{error}</p> : null}
      <button
        type="button"
        onClick={handleGoogleSignUp}
        className="rounded-md border border-border bg-surface px-3 py-2 text-sm font-medium hover:bg-background"
      >
        Continue with Google
      </button>
      <p className="text-center text-xs text-muted">
        Your workspace admin can also configure a different identity provider for this tenant.
      </p>
    </div>
  );
}
