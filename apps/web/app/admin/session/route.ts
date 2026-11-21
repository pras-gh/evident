import { NextResponse, type NextRequest } from "next/server";
import { ADMIN_COOKIE, adminGet, type Me } from "@/lib/admin";

/**
 * POST /admin/session — sign in with a platform admin's API key.
 * POST /admin/session?logout — sign out.
 *
 * The key is checked against the API (`GET /v1/me`), then kept in an httpOnly,
 * same-site-strict cookie scoped to /admin: page JavaScript cannot read it, and
 * other sites cannot send it. Server components forward it to the API.
 */
export async function POST(req: NextRequest) {
  const to = (path: string) => NextResponse.redirect(new URL(path, req.url), 303);

  if (req.nextUrl.searchParams.has("logout")) {
    const res = to("/admin/login");
    res.cookies.delete({ name: ADMIN_COOKIE, path: "/admin" });
    return res;
  }

  const key = String((await req.formData()).get("key") ?? "").trim();
  if (!key) return to("/admin/login?error=missing");

  let me: Me;
  try {
    me = await adminGet<Me>("/me", key);
  } catch {
    return to("/admin/login?error=invalid");
  }
  if (me.role !== "platform_admin" || !me.organization.is_internal) {
    return to("/admin/login?error=forbidden");
  }

  const res = to("/admin/ai-usage");
  res.cookies.set(ADMIN_COOKIE, key, {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
    path: "/admin",
    maxAge: 8 * 60 * 60,
  });
  return res;
}
