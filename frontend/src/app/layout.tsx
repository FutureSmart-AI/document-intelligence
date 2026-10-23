import type { Metadata, Viewport } from "next";

import { SessionProvider } from "@/data/session";
import { THEME_BOOTSTRAP } from "@/lib/constants";
import { DOC_FONT_CLASS } from "@/lib/fonts";
import CrossHostRedirect from "@/platform/components/CrossHostRedirect";
import { ReactQueryClientProvider } from "@/platform/components/ReactQueryClientProvider";
import { ToastProvider } from "@/platform/components/ToastContext";

import "./globals.css";

export const metadata: Metadata = {
  title:
    "Document Intelligence by FutureSmart AI | Add your documents, ask them anything",
  description:
    "Give it the documents you already have — contracts, resumes, invoices, reports. It reads every one of them, lays them out so you can find and sort what you need, and answers your questions from them, showing which document each answer came from.",
  applicationName: "Document Intelligence by FutureSmart AI",
  robots: "index, follow",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#ffffff",
};

/**
 * Stamps the stored theme on `<html>` before first paint.
 *
 * Without this the page renders in the system theme and then jumps to the stored
 * choice on hydration. Absent a stored choice nothing is stamped at all, which
 * is what leaves `prefers-color-scheme` in charge — see `globals.css`.
 */
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      {/* The three faces this product is drawn in — Plus Jakarta Sans for
          headings, Manrope for body copy, JetBrains Mono for ids and field
          values. `theme.css` binds them to `font-family` inside `di-root`. */}
      <body className={`${DOC_FONT_CLASS} antialiased`}>
        {/* Sends a route this host does not own to the one that does — the
            edge function cannot see a client-side navigation. */}
        <CrossHostRedirect />
        {/* Same query client, toast surface and session source as the agent UI:
            every view below reads live data through them. */}
        <ReactQueryClientProvider>
          <ToastProvider>
            <SessionProvider>{children}</SessionProvider>
          </ToastProvider>
        </ReactQueryClientProvider>
      </body>
    </html>
  );
}
