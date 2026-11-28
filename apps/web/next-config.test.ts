/**
 * A production build on Vercel stops rather than ship dev sign-in or an app
 * pointed at localhost; previews and local builds are left alone. And every
 * response carries the security headers.
 */
import { describe, expect, it } from "vitest";
import nextConfig, { productionProblems } from "./next.config.mjs";

const PROD = {
  VERCEL_ENV: "production",
  NEXT_PUBLIC_AUTH_PROVIDER: "firebase",
  API_URL: "https://api.ll.ax/v1",
  APP_URL: "https://app.ll.ax",
  NEXT_PUBLIC_FIREBASE_API_KEY: "web-key",
  NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "parallx.firebaseapp.com",
  NEXT_PUBLIC_FIREBASE_PROJECT_ID: "parallx",
  NEXT_PUBLIC_FIREBASE_APP_ID: "1:2:web:3",
};

describe("a production build", () => {
  it("goes ahead with real sign-in and https addresses", () => {
    expect(productionProblems(PROD)).toEqual([]);
  });

  it("stops on dev sign-in, a missing setting, or plain http", () => {
    expect(productionProblems({ ...PROD, NEXT_PUBLIC_AUTH_PROVIDER: "dev" }))
      .toEqual(["NEXT_PUBLIC_AUTH_PROVIDER must be firebase"]);
    expect(productionProblems({ ...PROD, NEXT_PUBLIC_FIREBASE_APP_ID: "" }))
      .toEqual(["NEXT_PUBLIC_FIREBASE_APP_ID is not set"]);
    expect(productionProblems({ ...PROD, API_URL: "http://localhost:8000/v1" }))
      .toEqual(["API_URL must be https://"]);
  });

  it("leaves previews and local builds alone", () => {
    expect(productionProblems({ VERCEL_ENV: "preview", NEXT_PUBLIC_AUTH_PROVIDER: "dev" }))
      .toEqual([]);
    expect(productionProblems({})).toEqual([]);
  });
});

describe("every response", () => {
  it("carries the security headers, and the API stays on the app's origin", async () => {
    const [rule] = await nextConfig.headers!();
    const names = rule.headers.map((h) => h.key);
    expect(rule.source).toBe("/:path*");
    expect(names).toEqual(expect.arrayContaining([
      "Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options",
      "Referrer-Policy", "Permissions-Policy"]));
    expect(nextConfig.poweredByHeader).toBe(false);
    const rewrites = await nextConfig.rewrites!();
    expect(rewrites).toEqual([{ source: "/api/:path*", destination: "http://localhost:8000/v1/:path*" }]);
  });
});
