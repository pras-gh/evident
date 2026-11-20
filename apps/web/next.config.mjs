/** @type {import('next').NextConfig} */

// API_URL is the API's /v1 base (tools/dev.sh sets http://localhost:8000/v1).
// It used to be appended to with another /v1, which proxied /api/x to /v1/v1/x.
const API = (process.env.API_URL ?? "http://localhost:8000/v1").replace(/\/+$/, "");
const V1 = API.endsWith("/v1") ? API : `${API}/v1`;

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // The API is a separate service; proxying keeps the browser on one origin,
    // so page images and the PDF load without CORS.
    return [{ source: "/api/:path*", destination: `${V1}/:path*` }];
  },
};
export default nextConfig;
