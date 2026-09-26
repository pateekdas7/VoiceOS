import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// ADR-005 §3: PlatformActor and TenantActor are structurally separate — a
// session for one must never grant routes under the other. Next.js 16 renamed
// `middleware` to `proxy`; this file replaces what would have been middleware.ts.
//
// Honest interim state: until the Web BFF (§4.1) issues real signed JWTs, this
// only checks for the *presence* of a session cookie and an actor_kind hint —
// it does not cryptographically verify anything yet. Every /admin and /client
// route handler must still independently verify the session against the BFF
// once it exists (defense in depth — never rely on proxy alone, per Next.js's
// own guidance that Server Functions can silently escape a matcher).

const SESSION_COOKIE = "voiceos_session";
const ACTOR_KIND_COOKIE = "voiceos_actor_kind";

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  const isAdminRoute = pathname.startsWith("/admin");
  const isClientRoute = pathname.startsWith("/client");
  if (!isAdminRoute && !isClientRoute) {
    return NextResponse.next();
  }

  const session = request.cookies.get(SESSION_COOKIE);
  if (!session) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", pathname);
    return NextResponse.redirect(loginUrl);
  }

  const actorKind = request.cookies.get(ACTOR_KIND_COOKIE)?.value;
  if (isAdminRoute && actorKind !== "platform") {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  if (isClientRoute && actorKind !== "tenant") {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/admin/:path*", "/client/:path*"],
};
