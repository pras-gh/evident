/**
 * Research pages need a session: without one, sign in and come back to the
 * page — query and all. The API checks every request itself; this only saves
 * a page that would fail.
 */
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "./middleware";

const req = (path: string, cookie?: string) =>
  new NextRequest(new URL(path, "http://t"), cookie ? { headers: { cookie } } : undefined);

describe("the middleware", () => {
  it("sends a signed-out reader to sign in, with the page to come back to", () => {
    for (const path of ["/search?q=nvidia", "/memory/nvda", "/timeline/msft", "/welcome",
                        "/account", "/billing/success?session_id=cs_1"]) {
      const res = middleware(req(path));
      const to = new URL(res.headers.get("location")!);
      expect(to.pathname).toBe("/signin");
      expect(to.searchParams.get("next")).toBe(path);
    }
  });

  it("lets a session through, and tells the page where it is", () => {
    const res = middleware(req("/search?q=nvidia", "parallx_session=pxs_abc"));
    expect(res.headers.get("location")).toBeNull();
    expect(res.headers.get("x-middleware-request-x-parallx-path")).toBe("/search?q=nvidia");
  });

  it("leaves public pages public", () => {
    for (const path of ["/", "/pricing", "/signin", "/about", "/terms"]) {
      expect(middleware(req(path)).headers.get("location")).toBeNull();
    }
  });
});
