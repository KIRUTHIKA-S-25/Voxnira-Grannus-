import type { NextConfig } from "next";

const isProd = process.env.NODE_ENV === "production";

// In production, 'unsafe-eval' is strictly eliminated to enforce strong XSS defenses.
// Turbopack / Fast Refresh requires eval only in local development.
const scriptSrc = isProd
  ? "script-src 'self' 'unsafe-inline';"
  : "script-src 'self' 'unsafe-eval' 'unsafe-inline';";

const securityHeaders = [
  {
    key: "X-DNS-Prefetch-Control",
    value: "on",
  },
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  },
  {
    key: "X-Frame-Options",
    value: "DENY",
  },
  {
    key: "X-Content-Type-Options",
    value: "nosniff",
  },
  {
    key: "Referrer-Policy",
    value: "strict-origin-when-cross-origin",
  },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(self), geolocation=(self)",
  },
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self';",
      scriptSrc,
      "style-src 'self' 'unsafe-inline';",
      "img-src 'self' data: blob: https:;",
      "font-src 'self' data:;",
      "connect-src 'self' http://localhost:8000 http://127.0.0.1:8000 https: data: blob: ws: wss:;",
      "media-src 'self' blob: data: https:;",
      "frame-ancestors 'none';",
    ].join(" "),
  },
];

const nextConfig: NextConfig = {
  output: "standalone",
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
