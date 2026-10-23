"use client";

import { useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import { EmptyNote, IconBtn, Pill } from "@/components/ui";
import type { DocumentSection } from "@/data/documentDetail";
import { cn } from "@/platform/utils";

import type { DocumentKind } from "./kind";
import type { TextMatch } from "./locate";

/**
 * The document itself, beside the values pulled out of it.
 *
 * It opens on the **extracted text**, not on the original, and that is the
 * whole point of the pane: the reason to look at a document from this screen is
 * to check whether a field came out right, and the fields were pulled out of
 * this text — not out of the PDF. The original is one click away and stays
 * exactly as it was uploaded.
 */
export default function DocumentViewer({
  fileName,
  kind,
  addedVia,
  sourceUrl,
  originalUrl,
  sections,
  loading,
  failed,
  failReason,
  highlight,
  onCopyAddress,
}: {
  fileName: string;
  kind: DocumentKind;
  /** How the document arrived — the same word the table's column prints. */
  addedVia: string;
  /** The address a website document was read from, when it is one. */
  sourceUrl: string | null;
  originalUrl: string | null;
  sections: DocumentSection[];
  loading: boolean;
  failed: boolean;
  failReason: string | null;
  /** A field value to point at in the extracted text, from "Show in text". */
  highlight: (TextMatch & { label: string }) | null;
  onCopyAddress: (address: string) => void;
}) {
  const [view, setView] = useState<"parsed" | "original">("parsed");

  // Asking where a value is means the extracted text, whichever tab was open —
  // the original is a PDF frame nothing can be pointed at inside.
  useEffect(() => {
    if (highlight) setView("parsed");
  }, [highlight]);

  return (
    <div className="flex min-w-0 flex-col border-line bg-sunken xl:border-r">
      <div className="flex flex-wrap items-center gap-2.5 border-b border-line bg-surface px-3.5 py-2.5">
        <div
          data-demo="document-views"
          className="inline-flex overflow-hidden rounded-di-md border border-line-strong"
          role="group"
          aria-label="What to show of this document"
        >
          {(
            [
              ["original", "Original", "The document as it was given to us"],
              [
                "parsed",
                "Extracted text",
                "The text we read out of it — this is what the fields were pulled from",
              ],
            ] as const
          ).map(([value, label, title]) => (
            <button
              key={value}
              type="button"
              title={title}
              aria-pressed={view === value}
              onClick={() => setView(value)}
              className={cn(
                "cursor-pointer px-2.5 py-1.5 text-[12.5px] font-semibold",
                view === value
                  ? "bg-accent-soft text-accent-softtext"
                  : "bg-surface text-ink-2 hover:bg-sunken",
              )}
            >
              {label}
            </button>
          ))}
        </div>
        <Pill tone="neutral">{kind.label}</Pill>
      </div>

      {/* Where this document came from. A web page gets its address, with the
          controls to copy or open it; a file gets its name and the connector
          that brought it in. */}
      <div className="flex flex-wrap items-center gap-2 border-b border-line-soft bg-sunken px-3.5 py-2 text-[11.5px] text-ink-2">
        <span className="text-[10px] font-bold tracking-[0.05em] text-ink-3 uppercase">
          Read from
        </span>
        {sourceUrl ? (
          <>
            <span className="min-w-30 flex-1 truncate font-mono text-ink">
              {sourceUrl}
            </span>
            <IconBtn
              label="Copy this address"
              className="size-6"
              onClick={() => onCopyAddress(sourceUrl)}
            >
              <Icon name="copy" className="size-3" />
            </IconBtn>
            <a
              href={sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 rounded-di-sm border border-line bg-surface px-2 py-1 text-[11.5px] font-semibold text-ink-2 hover:border-accent hover:text-accent"
            >
              Open page
              <Icon name="link" className="size-3" />
            </a>
          </>
        ) : (
          <>
            <span className="min-w-30 flex-1 truncate font-mono text-ink">
              {fileName}
            </span>
            <Pill tone="neutral">{addedVia}</Pill>
          </>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        {view === "parsed" ? (
          <ParsedText
            fileName={fileName}
            sections={sections}
            loading={loading}
            failed={failed}
            failReason={failReason}
            highlight={highlight}
          />
        ) : (
          <Original
            fileName={fileName}
            kind={kind}
            originalUrl={originalUrl}
            failed={failed}
          />
        )}
      </div>
    </div>
  );
}

/**
 * What the extractor actually read.
 *
 * This is the honest answer to "did it get my document right", and it is the
 * same text the fields were pulled out of — so a value that looks wrong is
 * checked here first, without downloading anything.
 */
function ParsedText({
  fileName,
  sections,
  loading,
  failed,
  failReason,
  highlight,
}: {
  fileName: string;
  sections: DocumentSection[];
  loading: boolean;
  failed: boolean;
  failReason: string | null;
  highlight: (TextMatch & { label: string }) | null;
}) {
  const markRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!highlight) return;
    const reduce = window.matchMedia?.(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    markRef.current?.scrollIntoView?.({
      block: "center",
      behavior: reduce ? "auto" : "smooth",
    });
  }, [highlight]);

  if (failed)
    return (
      <EmptyNote className="text-left">
        {failReason ?? "We could not read this document."}
        <span className="mt-2 block">
          There is no extracted text for the same reason there are no field
          values. The file itself is intact — download the original above to
          open it yourself.
        </span>
      </EmptyNote>
    );

  if (loading)
    return (
      <div className="flex flex-col gap-2">
        {[0, 1, 2, 3].map((row) => (
          <span
            key={row}
            className="block h-16 animate-pulse rounded-di-md bg-surface motion-reduce:animate-none"
            style={{ animationDelay: `${row * 90}ms` }}
          />
        ))}
        <span className="sr-only">Reading this document</span>
      </div>
    );

  if (!sections.length)
    return (
      <EmptyNote className="text-left">
        Nothing has been read out of this document yet. If it was added a moment
        ago, its text appears here once the extraction finishes.
      </EmptyNote>
    );

  const words = sections.reduce(
    (total, section) => total + section.text.trim().split(/\s+/).length,
    0,
  );

  const highlightIndex = highlight
    ? sections.findIndex((section) => section.id === highlight.sectionId)
    : -1;

  return (
    <div data-demo="document-text" className="mx-auto max-w-165">
      <p aria-live="polite" className="sr-only">
        {highlightIndex >= 0 && highlight
          ? `${highlight.label} is highlighted in section ${highlightIndex + 1}.`
          : ""}
      </p>
      <p className="mb-3 rounded-di-sm border border-line-soft bg-surface px-3 py-2 text-[11.5px] leading-relaxed text-ink-2">
        This is the text we read out of <b className="text-ink">{fileName}</b> —{" "}
        {sections.length} {sections.length === 1 ? "section" : "sections"},
        about {words.toLocaleString("en-US")} words. Every field in this
        collection was pulled out of exactly this text, so if a value looks
        wrong, this is where to check first.
      </p>
      {sections.map((section, index) => (
        <section
          key={section.id}
          className="mb-2 rounded-di-sm border border-line-soft bg-surface px-3.5 py-3"
        >
          <b className="mb-1.5 block text-[10.5px] font-bold tracking-[0.05em] text-ink-3 uppercase">
            Section {index + 1}
          </b>
          <pre className="m-0 font-sans text-[12.5px] leading-relaxed whitespace-pre-wrap text-ink">
            {highlight && index === highlightIndex ? (
              <>
                {section.text.slice(0, highlight.start)}
                <mark
                  ref={markRef}
                  title={`${highlight.label}, as it appears in the text`}
                  className="rounded-[3px] bg-accent-soft px-0.5 font-semibold text-accent-softtext ring-1 ring-accent"
                >
                  {section.text.slice(highlight.start, highlight.end)}
                </mark>
                {section.text.slice(highlight.end)}
              </>
            ) : (
              section.text
            )}
          </pre>
        </section>
      ))}
    </div>
  );
}

/**
 * The document exactly as it arrived.
 *
 * Embedded where the browser can render it, and never faked where it cannot:
 * the presigned link is served with its own disposition, so an embed that comes
 * back blank is a real possibility and the fallback says so and hands over the
 * link rather than leaving an empty frame.
 */
function Original({
  fileName,
  kind,
  originalUrl,
  failed,
}: {
  fileName: string;
  kind: DocumentKind;
  originalUrl: string | null;
  failed: boolean;
}) {
  if (!originalUrl)
    return (
      <EmptyNote className="text-left">
        {failed
          ? "This document could not be read, and no original was stored for it."
          : "There is no downloadable original for this document — it was pasted in as text, or read from a web page."}{" "}
        The extracted text beside this tab is what the fields were pulled from.
      </EmptyNote>
    );

  if (kind.embeddable)
    return (
      <object
        data={originalUrl}
        type="application/pdf"
        className="h-full min-h-125 w-full rounded-di-sm border border-line bg-surface"
        aria-label={`${fileName}, as it was uploaded`}
      >
        <div className="p-4">
          <EmptyNote className="text-left">
            Your browser will not display this file inline.{" "}
            <a
              href={originalUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="font-semibold text-accent underline underline-offset-2"
            >
              Open {fileName} in a new tab
            </a>
            .
          </EmptyNote>
        </div>
      </object>
    );

  return (
    <EmptyNote className="text-left">
      {fileName} is a {kind.label.toLowerCase()} file, which cannot be shown
      inline.{" "}
      <a
        href={originalUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="font-semibold text-accent underline underline-offset-2"
      >
        Open it in a new tab
      </a>
      , or read the extracted text beside this tab — that is what the fields
      were pulled from.
    </EmptyNote>
  );
}
