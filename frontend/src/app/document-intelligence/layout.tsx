"use client";

import RequireSession from "@/components/shell/RequireSession";

/**
 * Everything under `/document-intelligence` needs a session — the product has no public page of
 * its own any more, so this layout gates the whole tree. Providers themselves
 * are in the root layout: this app is nothing but this product, so they wrap
 * everything.
 */
export default function DocumentIntelligenceLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <RequireSession>{children}</RequireSession>;
}
