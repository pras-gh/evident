/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // The API is a separate service; proxy in dev so the browser sees one origin.
    return [{
      source: "/api/:path*",
      destination: `${process.env.API_URL ?? "http://localhost:8000"}/v1/:path*`,
    }];
  },
};
export default nextConfig;
