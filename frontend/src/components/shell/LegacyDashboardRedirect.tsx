"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * Forwards the product's old `/document-intelligence/dashboard/…` URLs to
 * their current home one level up.
 *
 * The `/dashboard` segment was dropped when the marketing page above it went
 * away and the collection board became the product's entry point. Bookmarks and links
 * pasted into tickets still carry the old shape, and the build is a static
 * export — there is no server to answer them with a 302 — so each retired path
 * keeps a page whose only job is this hop.
 *
 * The query string is read off `window.location` rather than through
 * `useSearchParams`, which would force every one of these stubs to carry a
 * Suspense boundary to prerender. `replace` keeps the dead URL out of the back
 * button.
 */
export default function LegacyDashboardRedirect() {
  const router = useRouter();

  useEffect(() => {
    const { pathname, search, hash } = window.location;
    router.replace(pathname.replace("/dashboard", "") + search + hash);
  }, [router]);

  return null;
}
