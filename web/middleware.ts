import { NextRequest, NextResponse } from "next/server";

// Cookie names must match agentsys.config.settings.session_cookie -- hand-
// mirrored, not shared, same convention as lib/api.ts's types (see that
// file's header comment).
//
// TWO names, because the API picks one at runtime: over HTTPS it sets
// "__Host-af_session" (the prefix is a browser-enforced guarantee that no
// subdomain can forge the cookie -- see that property's docstring), and
// over plain http:// dev it falls back to the bare name, since a browser
// silently drops a __Host- cookie that isn't Secure. Edge middleware can't
// read the API's config, so it accepts either.
//
// Checking both is safe HERE specifically because this is a presence-only
// routing hint, not an authorization decision -- it decides whether to show
// /login, nothing more. The API itself accepts exactly one name, so a
// forged bare cookie gets a 401 on the first real call and api.ts's central
// handler bounces back to /login (see lib/api.ts's redirectToLogin).
const SESSION_COOKIE_NAMES = ["__Host-af_session", "af_session"];

export function middleware(request: NextRequest) {
  const hasSession = SESSION_COOKIE_NAMES.some((name) => request.cookies.has(name));
  if (!hasSession) {
    const loginUrl = new URL("/login", request.url);
    return NextResponse.redirect(loginUrl);
  }
  return NextResponse.next();
}

export const config = {
  // Everything except /login itself, Next's static/image internals, and the
  // favicon -- a logged-out visitor hitting any dashboard route lands on
  // /login instead of a 401'd blank page.
  matcher: ["/((?!login|_next/static|_next/image|favicon.ico).*)"],
};
