"use client";

import { useEffect, useState } from "react";

import { ChevronDown, FileText, Hash, Layers, Sparkles } from "lucide-react";

import type { Source } from "../types/source";
import { cn } from "../utils/cn";
import { useRenderMarkdown, useSourcesTheme } from "./context";
import DocumentMetadataTable from "./DocumentMetadataTable";
import { Tag } from "./primitives";
import RelatedChunksList from "./RelatedChunksList";
import type { SourcesViewMode } from "./SourcesToolbar";

interface SourceCardProps {
  source: Source;
  index: number;
  /** Which sections to show: "all" | "document" (summary+metadata) | "chunks". */
  viewMode?: SourcesViewMode;
  forceOpen?: boolean;
  /** Initial open state when no expand-all override is active. */
  defaultOpen?: boolean;
}

/** Pull a short descriptor off generic metadata for the subtitle line. */
const descriptor = (metadata?: Record<string, unknown>): string[] => {
  if (!metadata) return [];
  return ["document_type", "section"]
    .map((k) => metadata[k])
    .filter((v): v is string => typeof v === "string" && v.trim() !== "");
};

const SourceCard = ({
  source,
  index,
  viewMode = "all",
  forceOpen,
  defaultOpen = false,
}: SourceCardProps) => {
  const theme = useSourcesTheme();
  const renderMarkdown = useRenderMarkdown();
  const [open, setOpen] = useState(defaultOpen);

  useEffect(() => {
    if (forceOpen !== undefined) setOpen(forceOpen);
  }, [forceOpen]);

  const chunkCount = source.chunks?.length ?? 0;
  const summary = source.summary || source.context;
  const subtitle = descriptor(source.metadata);

  // The view filter selects which parts of the card render.
  // "document" → summary + metadata, "chunks" → chunks, "all" → everything.
  const showSummary = viewMode !== "chunks";
  const showMetadata = viewMode !== "chunks";
  const showChunks = viewMode !== "document";

  return (
    <div
      className={cn(
        "overflow-hidden rounded-[10px] border transition-colors",
        theme.surface,
        theme.cardHover,
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-start gap-3 px-4 py-3.5 text-left"
      >
        <span
          className={cn(
            "flex size-[26px] shrink-0 items-center justify-center rounded-full text-[12.5px] font-bold",
            theme.indexBadge,
          )}
        >
          {index + 1}
        </span>

        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <FileText className={cn("size-3.5 shrink-0", theme.accentText)} />
            <span
              className={cn(
                "truncate text-sm font-semibold underline decoration-[#5B5BD6]/35 underline-offset-[3px]",
                theme.accentText,
              )}
            >
              {source.file_name}
            </span>
            {(source.source_type || source.retrieval_mode) && (
              <Tag tone="outline">
                <Layers className={cn("size-2.5", theme.textFaint)} />
                {source.source_type || source.retrieval_mode}
              </Tag>
            )}
            <Tag tone="outline">
              <Hash className={cn("size-2.5", theme.textFaint)} />
              {chunkCount} {chunkCount === 1 ? "chunk" : "chunks"}
            </Tag>
          </span>
          <span
            className={cn(
              "mt-1 flex items-center gap-1.5 text-[11.5px] font-medium",
              theme.textFaint,
            )}
          >
            <Sparkles className="size-2.5" />
            Document summary
            {subtitle.map((d) => (
              <span key={d} className="flex items-center gap-1.5">
                <span aria-hidden>·</span>
                <span className={theme.textMuted}>{d}</span>
              </span>
            ))}
          </span>
        </span>

        <ChevronDown
          className={cn(
            "size-4 shrink-0 transition-transform",
            theme.textMuted,
            open && "rotate-180",
          )}
        />
      </button>

      {showSummary && summary && (
        <div
          className={cn(
            "max-w-none pb-3.5 pl-[54px] pr-4 text-[13.5px] leading-relaxed",
            theme.prose,
            theme.textBody,
          )}
        >
          {renderMarkdown(summary)}
        </div>
      )}

      {open && (
        <div className="flex flex-col gap-2.5 pb-4 pl-[54px] pr-4">
          {showMetadata && (
            <DocumentMetadataTable
              metadata={source.metadata}
              forceOpen={forceOpen}
              defaultOpen={defaultOpen}
            />
          )}
          {showChunks && (
            <RelatedChunksList
              chunks={source.chunks}
              forceOpen={forceOpen}
              defaultOpen={defaultOpen}
            />
          )}
        </div>
      )}
    </div>
  );
};

export default SourceCard;
