/** @type {import('next').NextConfig} */
const backend = process.env.BACKEND_INTERNAL_URL || "http://localhost:8000";

const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  skipTrailingSlashRedirect: true, // Django API URLs end with "/"
  // In development the Next server proxies API/WebSocket paths to Django; in production Nginx does.
  async rewrites() {
    return process.env.NODE_ENV === "production"
      ? []
      : [
          { source: "/api/:path*/", destination: `${backend}/api/:path*/` },
          { source: "/api/:path*", destination: `${backend}/api/:path*/` },
          { source: "/healthz/", destination: `${backend}/healthz/` },
        ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
      { source: "/sw.js", headers: [{ key: "Cache-Control", value: "no-cache" }, { key: "Service-Worker-Allowed", value: "/" }] },
    ];
  },
};

export default nextConfig;
