import { NextResponse, type NextRequest } from "next/server";

/** /admin/* needs the admin cookie; the login page and the session handler do
 *  not. The API checks the key itself on every request — this only saves a
 *  round trip to a page that would fail. */
export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (pathname === "/admin/login" || pathname.startsWith("/admin/session")) {
    return NextResponse.next();
  }
  if (!req.cookies.get("evident_admin")?.value) {
    const url = req.nextUrl.clone();
    url.pathname = "/admin/login";
    url.search = "";
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = { matcher: ["/admin/:path*"] };
