import { NextResponse, type NextRequest } from "next/server";

const SESSION = "parallx_session";

/** Research needs an account: these go to sign-in first, and come back. */
const RESEARCH = ["/search", "/memory", "/timeline", "/evidence", "/welcome", "/account",
                  "/billing/success"];

/**
 * Two jobs, both only to save a round trip — the API checks every request
 * itself:
 *
 * - /admin/* needs the admin cookie (the login page and session handler do not);
 * - research pages need a session cookie; without one, sign in and return to
 *   the page asked for — query and all, so a search is never typed twice.
 *
 * Every page also learns its own path (`x-parallx-path`), so a session that
 * expires mid-visit can send the reader to sign in and straight back.
 */
export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;

  if (pathname.startsWith("/admin")) {
    if (pathname === "/admin/login" || pathname.startsWith("/admin/session")) {
      return NextResponse.next();
    }
    if (!req.cookies.get("parallx_admin")?.value) {
      const url = req.nextUrl.clone();
      url.pathname = "/admin/login";
      url.search = "";
      return NextResponse.redirect(url);
    }
    return NextResponse.next();
  }

  const here = `${pathname}${search}`;
  if (RESEARCH.some((p) => pathname === p || pathname.startsWith(`${p}/`)) &&
      !req.cookies.get(SESSION)?.value) {
    const url = req.nextUrl.clone();
    url.pathname = "/signin";
    url.search = `?next=${encodeURIComponent(here)}`;
    return NextResponse.redirect(url);
  }

  const headers = new Headers(req.headers);
  headers.set("x-parallx-path", here);
  return NextResponse.next({ request: { headers } });
}

export const config = {
  // every page, not the API proxy, Next's assets, or files with an extension
  matcher: ["/((?!api/|_next/|.*\\.[a-zA-Z0-9]+$).*)"],
};
