"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { BookOpen } from "lucide-react";

import type { Source } from "../types/source";
import { cn } from "../utils/cn";
import { SourcesProvider, type RenderMarkdown } from "./context";
import SourceCard from "./SourceCard";
import SourcesToolbar, { type SourcesViewMode } from "./SourcesToolbar";
import { slateSourcesTheme, type SourcesTheme } from "./theme";
import { useIncrementalList } from "./useIncrementalList";

const INITIAL = 4;
const STEP = 4;

export interface SourcesPanelProps {
  sources: Source[];
  /** Palette preset; defaults to the dashboard slate/indigo look. */
  theme?: SourcesTheme;
  /** Host markdown renderer for summaries + chunk content; defaults to plain text. */
  renderMarkdown?: RenderMarkdown;
}

const SourcesPanel = ({
  sources,
  theme = slateSourcesTheme,
  renderMarkdown = (text) => text,
}: SourcesPanelProps) => {
  const [query, setQuery] = useState("");
  const [viewMode, setViewMode] = useState<SourcesViewMode>("all");
  const [allExpanded, setAllExpanded] = useState<boolean | undefined>(
    undefined,
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sources;
    return sources.filter((s) => (s.file_name || "").toLowerCase().includes(q));
  }, [sources, query]);

  const { visibleItems, hasMore, loadMore } = useIncrementalList(
    filtered,
    INITIAL,
    STEP,
  );

  const sentinelRef = useRef<HTMLDivElement | null>(null);

  // Auto-load the next batch when the sentinel scrolls into the viewport.
  // Pure enhancement: the Load more button does the same, so SSR/jsdom (no
  // IntersectionObserver) still work.
  useEffect(() => {
    const el = sentinelRef.current;
    if (!hasMore || typeof IntersectionObserver === "undefined" || !el) {
      return undefined;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) loadMore();
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [hasMore, loadMore]);

  const handleExport = () => {
    const json = JSON.stringify(filtered, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "sources.json";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  return (
    <SourcesProvider theme={theme} renderMarkdown={renderMarkdown}>
      <div
        className={cn("mt-3 overflow-hidden rounded-xl border", theme.panel)}
      >
        {/* Header */}
        <div
          className={cn(
            "flex flex-wrap items-center gap-2.5 border-b px-4 py-3",
            theme.border,
            theme.panelHeaderBg,
          )}
        >
          <BookOpen className={cn("size-3.5 shrink-0", theme.accentText)} />
          <h4 className={cn("text-[13.5px] font-semibold", theme.textPrimary)}>
            Referenced Sources
          </h4>
          <span
            className={cn(
              "rounded-full px-2 py-px text-[11px] font-semibold",
              theme.countBadge,
            )}
          >
            {sources.length}
          </span>
          <div className="ml-auto">
            <SourcesToolbar
              query={query}
              onQueryChange={setQuery}
              viewMode={viewMode}
              onViewModeChange={setViewMode}
              allExpanded={!!allExpanded}
              onToggleExpandAll={() => setAllExpanded((prev) => !prev)}
              onExport={handleExport}
            />
          </div>
        </div>

        {/* Body */}
        <div className={cn("p-3.5", theme.bodyInset)}>
          {filtered.length === 0 ? (
            <div
              className={cn(
                "rounded-lg border border-dashed p-10 text-center text-[13px]",
                theme.emptyState,
              )}
            >
              No sources match your filter.
            </div>
          ) : (
            <>
              <div className="flex flex-col gap-2.5">
                {visibleItems.map((source, i) => (
                  <SourceCard
                    key={source.document_id ?? `${source.file_name}_${i}`}
                    source={source}
                    index={i}
                    viewMode={viewMode}
                    forceOpen={allExpanded}
                    defaultOpen={i === 0}
                  />
                ))}
              </div>

              {/* Footer */}
              <div
                className={cn(
                  "mt-3 flex items-center justify-between gap-3 px-1 text-[11.5px]",
                  theme.textFaint,
                )}
              >
                <span>
                  Showing {visibleItems.length} of {filtered.length} sources
                </span>
                {hasMore && (
                  <button
                    type="button"
                    onClick={loadMore}
                    aria-label="Load more sources"
                    className={cn(
                      "rounded-md border px-2.5 py-1 font-semibold",
                      theme.control,
                      theme.accentText,
                      theme.hoverSurface,
                    )}
                  >
                    Load more
                  </button>
                )}
              </div>
              <div ref={sentinelRef} aria-hidden className="h-px w-full" />
            </>
          )}
        </div>
      </div>
    </SourcesProvider>
  );
};

export default SourcesPanel;
