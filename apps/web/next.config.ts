import type { NextConfig } from "next";

/**
 * Response headers for every page (`REQ-SEC-003`, Phase 8 hardening).
 *
 * The policy is deliberately narrow rather than a full script CSP: it forbids
 * framing, plugins, `<base>` rewriting and form posts to other origins, which
 * are the attacks a research product's pages actually face, without pinning
 * `connect-src` to an API and storage origin that differ per deployment — a
 * wrong guess there would break uploads silently rather than loudly.
 */
const securityHeaders = [
  {
    key: "Content-Security-Policy",
    value: "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'",
  },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  ...(process.env.VERCEL_ENV === "production"
    ? [{ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" }]
    : []),
];

const nextConfig: NextConfig = {
  turbopack: {
    root: __dirname,
  },
  devIndicators: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
