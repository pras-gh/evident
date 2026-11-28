/** @type {import('next').NextConfig} */

// API_URL is the API's /v1 base (tools/dev.sh sets http://localhost:8000/v1).
// It used to be appended to with another /v1, which proxied /api/x to /v1/v1/x.
const API = (process.env.API_URL ?? "http://localhost:8000/v1").replace(/\/+$/, "");
const V1 = API.endsWith("/v1") ? API : `${API}/v1`;

/**
 * What a production build must be given: real sign-in, and where the API and
 * the app live. Vercel marks its production deployments VERCEL_ENV=production;
 * missing any of these fails that build, rather than shipping dev sign-in
 * (which accepts anyone who types an email address) or an app pointed at
 * localhost. Returns the problems, so it can be tested.
 */
export function productionProblems(env = process.env) {
  if (env.VERCEL_ENV !== "production") return [];
  const problems = [];
  if (env.NEXT_PUBLIC_AUTH_PROVIDER !== "firebase") {
    problems.push("NEXT_PUBLIC_AUTH_PROVIDER must be firebase");
  }
  for (const name of ["API_URL", "APP_URL", "NEXT_PUBLIC_FIREBASE_API_KEY",
                      "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN", "NEXT_PUBLIC_FIREBASE_PROJECT_ID",
                      "NEXT_PUBLIC_FIREBASE_APP_ID"]) {
    if (!env[name]) problems.push(`${name} is not set`);
  }
  for (const name of ["API_URL", "APP_URL"]) {
    if (env[name] && !env[name].startsWith("https://")) problems.push(`${name} must be https://`);
  }
  return problems;
}

const problems = productionProblems();
if (problems.length) {
  throw new Error(`Production build stopped. In the Vercel project's environment variables: ${
    problems.join("; ")}.`);
}

/** Sent with every page and every proxied API response. */
const SECURITY_HEADERS = [
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  // the app may frame itself (a filing's PDF); nobody else may frame it
  { key: "X-Frame-Options", value: "SAMEORIGIN" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), usb=()" },
];

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: SECURITY_HEADERS }];
  },
  async rewrites() {
    // The API is a separate service; proxying keeps the browser on one origin,
    // so page images and the PDF load without CORS, and the session cookie
    // belongs to the app's domain.
    return [{ source: "/api/:path*", destination: `${V1}/:path*` }];
  },
};
export default nextConfig;
