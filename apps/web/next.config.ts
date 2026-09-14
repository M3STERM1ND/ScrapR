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

/**
 * The API, proxied under this origin (`/api/v1/*` → `<API>/v1/*`).
 *
 * The session cookies are `SameSite=Lax`, which a browser neither stores from
 * nor sends to a cross-site request — and a `*.vercel.app` page calling a
 * `*.up.railway.app` API is cross-site. Served from the web app's own origin,
 * the cookie is first-party and the security design stays exactly as it is.
 *
 * Server-only and read at build time, when the route table is written: the
 * browser only ever sees `/api`. Unset, there is no rewrite and the client
 * calls `NEXT_PUBLIC_API_BASE_URL` directly, which is local development.
 *
 * Only `/v1` is proxied, so the API's docs, schema and health check are not
 * mirrored onto the public site.
 */
const apiOrigin = process.env.SCRAPR_API_ORIGIN
  ? new URL(process.env.SCRAPR_API_ORIGIN).origin
  : null;

if (process.env.NEXT_PUBLIC_API_BASE_URL?.startsWith("/") && apiOrigin === null) {
  // A same-origin base with nothing behind it would 404 every API call, and the
  // client would report that as the service being unavailable.
  throw new Error(
    "NEXT_PUBLIC_API_BASE_URL is a same-origin path, so SCRAPR_API_ORIGIN must name the API to proxy to.",
  );
}

const nextConfig: NextConfig = {
  turbopack: {
    root: __dirname,
  },
  devIndicators: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  async rewrites() {
    return apiOrigin
      ? [{ source: "/api/v1/:path*", destination: `${apiOrigin}/v1/:path*` }]
      : [];
  },
};

export default nextConfig;
