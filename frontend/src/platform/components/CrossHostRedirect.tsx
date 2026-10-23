"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";

import { crossHostUrlFor } from "@/platform/lib/host-routing";

/**
 * Keeps client-side navigation on the host that owns the route.
 *
 * The CloudFront function splits the two hosts by product, but it only sees
 * real requests — a `<Link>` or `router.push` into a product path (or out of
 * one, back to the dashboard) never reaches the edge, and both hosts serve the
 * same bucket, so the page renders on the wrong host without complaint. This
 * catches that case wherever it happens instead of relying on every call site
 * spelling out an absolute URL: `window.location.replace` turns the SPA hop
 * into a real request on the right origin, and keeps the wrong-host URL out of
 * the back button.
 *
 * `window.location` is read directly rather than through `useSearchParams`,
 * which a static export would need a Suspense boundary for; `usePathname` is
 * only here to re-run the check on every navigation.
 */
export default function CrossHostRedirect() {
  const pathname = usePathname();

  useEffect(() => {
    const target = crossHostUrlFor(window.location);
    if (target) window.location.replace(target);
  }, [pathname]);

  return null;
}
