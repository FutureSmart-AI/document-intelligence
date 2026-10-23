#!/usr/bin/env bun
/**
 * Serve the static export in `out/` the way the CDN does.
 *
 * `next start` cannot serve an `output: "export"` build, and a plain static
 * server gets two things wrong that the deploy depends on:
 *
 * - assets are referenced as `/document-intelligence/_next/…` (`assetPrefix`)
 *   while the build writes them to `out/_next/…`, and the deploy uploads them
 *   under the prefix — so the prefix is stripped here;
 * - routes are directories (`trailingSlash: true`), so `/document-intelligence/knowledge/`
 *   resolves to its `index.html`.
 *
 * `/api/knowledge/*` is forwarded to `KNOWLEDGE_API_ORIGIN` when that is set,
 * as CloudFront forwards it in production; otherwise it answers 502, which is
 * what the e2e suite wants (it stubs every API call in the browser).
 *
 * Usage: bun run build && bun run preview [--port 3002]
 */
import { existsSync, statSync } from "node:fs";
import { join, normalize } from "node:path";

const OUT = join(import.meta.dir, "..", "out");
const PREFIX = "/document-intelligence";
const portFlag = process.argv.indexOf("--port");
const PORT = Number(
  portFlag > -1 ? process.argv[portFlag + 1] : (process.env.PORT ?? 3002),
);
const API_ORIGIN = process.env.KNOWLEDGE_API_ORIGIN;

if (!existsSync(join(OUT, "document-intelligence", "index.html"))) {
  console.error("No static export in out/ — run `bun run build` first.");
  process.exit(1);
}

function fileFor(pathname: string): string | null {
  let rel = decodeURIComponent(pathname);
  if (rel.startsWith(`${PREFIX}/_next/`)) rel = rel.slice(PREFIX.length);
  const candidate = normalize(join(OUT, rel));
  if (!candidate.startsWith(OUT)) return null;
  if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
  const index = join(candidate, "index.html");
  return existsSync(index) ? index : null;
}

Bun.serve({
  port: PORT,
  async fetch(request) {
    const url = new URL(request.url);

    if (url.pathname.startsWith("/api/knowledge/")) {
      if (!API_ORIGIN)
        return new Response("No KNOWLEDGE_API_ORIGIN", { status: 502 });
      const upstream = new URL(url.pathname + url.search, API_ORIGIN);
      return fetch(upstream, {
        method: request.method,
        headers: request.headers,
        body: request.body,
        redirect: "manual",
      });
    }

    // Directory routes need their trailing slash, as S3 website hosting and
    // `trailingSlash: true` both expect.
    if (!url.pathname.endsWith("/") && !url.pathname.includes(".")) {
      return Response.redirect(`${url.pathname}/${url.search}`, 308);
    }

    const file = fileFor(url.pathname);
    if (file) return new Response(Bun.file(file));
    const notFound = join(OUT, "404.html");
    return new Response(Bun.file(notFound), { status: 404 });
  },
});

console.log(`Serving out/ at http://localhost:${PORT}${PREFIX}/`);
