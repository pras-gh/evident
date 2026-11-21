// @vitest-environment node
/** Signing in to the admin dashboard: the key is checked against the API and
 *  kept only in an httpOnly cookie; anything but a platform admin is refused. */
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { middleware } from "@/middleware";
import { POST } from "./route";

const me = (role: string, is_internal: boolean) => ({
  user_id: "u", email: "a@example.com", role,
  organization: { id: "o", slug: "evident-internal", name: "Evident", is_internal },
});

function login(key: string) {
  const body = new URLSearchParams({ key });
  return POST(new NextRequest("http://localhost:3000/admin/session", {
    method: "POST", body, headers: { "content-type": "application/x-www-form-urlencoded" },
  }));
}

afterEach(() => vi.unstubAllGlobals());

describe("POST /admin/session", () => {
  it("a platform admin's key is checked with the API and stored httpOnly", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify(me("platform_admin", true))));
    vi.stubGlobal("fetch", fetch);
    const res = await login("evk_valid_key_123");
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("http://localhost:3000/admin/ai-usage");
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/me$/);
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer evk_valid_key_123");
    const cookie = res.headers.get("set-cookie") ?? "";
    expect(cookie).toMatch(/^evident_admin=evk_valid_key_123;/);
    expect(cookie).toMatch(/HttpOnly/i);
    expect(cookie).toMatch(/SameSite=strict/i);
    expect(cookie).toMatch(/Path=\/admin/);
  });

  it("refuses a valid key that is not a platform admin, without a cookie", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(me("org_admin", false)))));
    const res = await login("evk_customer");
    expect(res.headers.get("location")).toBe("http://localhost:3000/admin/login?error=forbidden");
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("refuses a key the API rejects", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    const res = await login("evk_wrong");
    expect(res.headers.get("location")).toBe("http://localhost:3000/admin/login?error=invalid");
  });

  it("refuses an empty form without calling the API", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const res = await login("  ");
    expect(res.headers.get("location")).toBe("http://localhost:3000/admin/login?error=missing");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("signing out clears the cookie", async () => {
    const res = await POST(new NextRequest("http://localhost:3000/admin/session?logout", { method: "POST" }));
    expect(res.headers.get("location")).toBe("http://localhost:3000/admin/login");
    expect(res.headers.get("set-cookie")).toMatch(/evident_admin=;.*Path=\/admin/);
  });
});

describe("middleware", () => {
  const go = (path: string, cookie?: string) => middleware(new NextRequest(
    `http://localhost:3000${path}`, cookie ? { headers: { cookie: `evident_admin=${cookie}` } } : {}));

  it("sends anyone without the cookie to sign in", () => {
    expect(go("/admin/ai-usage?provider=groq").headers.get("location"))
      .toBe("http://localhost:3000/admin/login");
  });

  it("lets the sign-in page and handler through, and signed-in admins", () => {
    expect(go("/admin/login").headers.get("location")).toBeNull();
    expect(go("/admin/session").headers.get("location")).toBeNull();
    expect(go("/admin/ai-usage", "evk_x").headers.get("location")).toBeNull();
  });
});
