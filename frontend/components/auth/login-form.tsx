"use client";

import { useState } from "react";

// VoiceOS has no first-party password storage anywhere in the backend
// (AuthService accepts mTLS/JWT/API-key only; tokens are minted exclusively
// by OIDCProvider.exchange_code() against a tenant's own IdP). Sign-in is
// therefore IdP-only — this form has no password field by design, not by
// omission. Actor kind (platform vs. tenant) is resolved server-side from
// which IdP/account the callback belongs to; this form never asserts it.

// The BFF's google_callback redirects failures here as ?error=<code> (see
// api.py) -- these are the only codes it ever sends.
const ERROR_MESSAGES: Record<string, string> = {
  no_account: "No VoiceOS account found for that Google identity. Ask your admin to invite you.",
  already_registered: "That email is already registered. Just sign in again below.",
  missing_code: "Google didn't return an authorization code. Please try again.",
  google_auth_failed: "Google sign-in failed. Please try again.",
};

// Reads window.location.search directly (not useSearchParams) -- same
// reasoning as handleGoogleSignIn's `next` read below: this keeps the
// component out of a Suspense boundary. Guarded for the server-rendered
// first pass, where `window` doesn't exist yet.
function initialErrorMessage(): string | null {
  if (typeof window === "undefined") return null;
  const code = new URLSearchParams(window.location.search).get("error");
  if (!code) return null;
  return ERROR_MESSAGES[code] ?? `Sign-in failed (${code}).`;
}

export function LoginForm() {
  const [error, setError] = useState<string | null>(initialErrorMessage);
  const bffUrl = process.env.NEXT_PUBLIC_BFF_URL;

  function handleGoogleSignIn() {
    if (!bffUrl) {
      setError("Backend not configured (NEXT_PUBLIC_BFF_URL is unset) — cannot sign in yet.");
      return;
    }
    // The OIDC round-trip is a full server redirect, not a fetch — "next" is
    // read directly off the current URL (not useSearchParams) so this
    // component needs no Suspense boundary.
    const next = new URLSearchParams(window.location.search).get("next");
    const startUrl = new URL(`${bffUrl}/auth/google/start`);
    if (next) startUrl.searchParams.set("next", next);
    window.location.href = startUrl.toString();
  }

  return (
    <div className="flex w-full max-w-sm flex-col gap-4">
      {error ? <p className="text-sm text-status-critical">{error}</p> : null}
      <button
        type="button"
        onClick={handleGoogleSignIn}
        className="rounded-md border border-border bg-surface px-3 py-2 text-sm font-medium hover:bg-background"
      >
        Continue with Google
      </button>
      <p className="text-center text-xs text-muted">
        Your workspace admin can also configure a different identity provider (Okta, Azure AD, etc.)
        for tenant sign-in.
      </p>
    </div>
  );
}
