import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

/**
 * Where `next dev` forwards `/api/knowledge/*`.
 *
 * Server-side only (no `NEXT_PUBLIC_`), and only read by the dev server: a
 * static export has no server at request time, so in every deployed build the
 * browser calls `/api/knowledge/*` on the origin that served the page and the
 * CDN routes it. See `../docs/configuration.md`.
 */
const KNOWLEDGE_API_ORIGIN =
  process.env.KNOWLEDGE_API_ORIGIN || "http://localhost:8000";

/**
 * Document Intelligence UI — a static export shipped to S3 + CloudFront, so no
 * server runtime exists at request time. Dynamic route segments would need
 * `generateStaticParams`; this app uses flat routes plus a `?collection=` query
 * parameter instead.
 */
export default function nextConfig(phase: string): NextConfig {
  const config: NextConfig = {
    trailingSlash: true,
    output: "export",
    /**
     * Assets are requested from `/document-intelligence/_next/…`, not `/_next/…`.
     *
     * On a shared host every product is a path, and a CloudFront behaviour per
     * product points at that product's bucket. An unprefixed `/_next/static/…`
     * would not match this product's behaviour and would be served from
     * another build. The prefix keeps a page and its own JavaScript together.
     *
     * `basePath` is deliberately *not* used: the routes already live under
     * `src/app/document-intelligence/`, so a base path would nest them twice.
     * The deploy workflow uploads `out/` under the same prefix.
     */
    assetPrefix: "/document-intelligence",
    images: { unoptimized: true },
    compiler: {
      removeConsole:
        process.env.NODE_ENV === "production"
          ? { exclude: ["error", "warn"] }
          : false,
    },
    experimental: {
      optimizePackageImports: ["lodash-es", "@tanstack/react-query", "dayjs"],
    },
  };

  if (phase !== PHASE_DEVELOPMENT_SERVER) return config;

  /**
   * Development only: the page's own origin answers `/api/knowledge/*` by
   * proxying it, which is what CloudFront does in production. The request —
   * cookies included — is forwarded as the browser sent it, so it
   * authenticates using the API's configured local or hosted session profile.
   * Independent development uses NEXT_PUBLIC_AUTH_MODE=standalone and the
   * local API; hosted SSO requires its configured origin and cookie domain.
   */
  return {
    ...config,
    output: undefined,
    async rewrites() {
      return [
        {
          source: "/api/knowledge/:path*",
          destination: `${KNOWLEDGE_API_ORIGIN}/api/knowledge/:path*`,
        },
      ];
    },
  };
}
