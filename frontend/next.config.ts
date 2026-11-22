import type { NextConfig } from "next";

const isProd = process.env.NODE_ENV === "production";

function publicOrigin(envName: string): string {
  const value = process.env[envName]?.trim();
  if (!value) throw new Error(`${envName} must be set before building the production frontend.`);
  const parsed = new URL(value);
  if (
    parsed.protocol !== "https:" || !parsed.hostname || parsed.username || parsed.password ||
    parsed.pathname !== "/" || parsed.search || parsed.hash ||
    /yourdomain|your-project|example|localhost/i.test(parsed.hostname)
  ) {
    throw new Error(`${envName} must be a deployed HTTPS URL without a path or placeholder host.`);
  }
  return parsed.origin;
}

function validatePublicProductionConfiguration(): { apiOrigin: string; supabaseOrigin: string } | null {
  if (!isProd) return null;
  const apiOrigin = publicOrigin("NEXT_PUBLIC_API_URL");
  const supabaseOrigin = publicOrigin("NEXT_PUBLIC_SUPABASE_URL");
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim() ?? "";
  const grievanceEmail = process.env.NEXT_PUBLIC_GRIEVANCE_OFFICER_EMAIL?.trim() ?? "";
  if (!anonKey || /your_|example|placeholder|replace/i.test(anonKey)) {
    throw new Error("NEXT_PUBLIC_SUPABASE_ANON_KEY must be set to the project's publishable/anon key.");
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(grievanceEmail) || /example|your/i.test(grievanceEmail)) {
    throw new Error("NEXT_PUBLIC_GRIEVANCE_OFFICER_EMAIL must be set to the real public contact email.");
  }
  return { apiOrigin, supabaseOrigin };
}

const productionPublicConfig = validatePublicProductionConfiguration();

// In production, 'unsafe-eval' is strictly eliminated to enforce strong XSS defenses.
// Turbopack / Fast Refresh requires eval only in local development.
const scriptSrc = isProd
  ? "script-src 'self' 'unsafe-inline';"
  : "script-src 'self' 'unsafe-eval' 'unsafe-inline';";
const connectSrc = productionPublicConfig
  ? `connect-src 'self' ${productionPublicConfig.apiOrigin} ${productionPublicConfig.supabaseOrigin} wss://${new URL(productionPublicConfig.supabaseOrigin).hostname};`
  : "connect-src 'self' http://localhost:8000 http://127.0.0.1:8000 https: data: blob: ws: wss:;";

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
      connectSrc,
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
