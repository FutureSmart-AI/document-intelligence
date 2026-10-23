"use client";

import { useEffect, useState } from "react";

import { ChevronDown, Info } from "lucide-react";

import type { SourceChunk } from "../types/source";
import { cn } from "../utils/cn";
import ChunkContent from "./ChunkContent";
import { useSourcesTheme } from "./context";
import MetadataTable from "./MetadataTable";
import { Tag } from "./primitives";

interface ChunkRowProps {
  chunk: SourceChunk;
  forceOpen?: boolean;
  /** Initial open state when no expand-all override is active. */
  defaultOpen?: boolean;
}

/** Human-readable chunk title: topic, then metadata, then first words of body. */
const deriveChunkName = (chunk: SourceChunk): string => {
  if (chunk.topic?.trim()) return chunk.topic.trim();
  const meta = chunk.metadata as Record<string, unknown> | undefined;
  const named = meta?.chunk_name ?? meta?.section;
  if (typeof named === "string" && named.trim()) return named.trim();
  const raw = (chunk.text || "")
    .replace(/^#+\s*/, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!raw) return chunk.chunk_id || "Section";
  const words = raw.split(" ").slice(0, 9).join(" ");
  return words + (raw.length > words.length ? "…" : "");
};

const ChunkRow = ({ chunk, forceOpen, defaultOpen = false }: ChunkRowProps) => {
  const theme = useSourcesTheme();
  const [open, setOpen] = useState(defaultOpen);
  const [showMeta, setShowMeta] = useState(defaultOpen);

  useEffect(() => {
    if (forceOpen !== undefined) {
      setOpen(forceOpen);
      setShowMeta(forceOpen);
    }
  }, [forceOpen]);

  const name = deriveChunkName(chunk);
  const hasScore = typeof chunk.score === "number";
  const hasPosition =
    chunk.position &&
    typeof chunk.position.index === "number" &&
    typeof chunk.position.total === "number";

  return (
    <div className={cn("overflow-hidden rounded-lg border", theme.surface)}>
      <div
        className={cn(
          "flex items-center gap-2.5 px-3 py-2.5",
          open && cn("border-b", theme.border, theme.panelHeaderBg),
        )}
      >
        {chunk.chunk_id && (
          <span
            className={cn(
              "shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px] font-semibold",
              theme.chip,
            )}
          >
            {chunk.chunk_id}
          </span>
        )}
        <span
          className={cn(
            "min-w-0 flex-1 truncate text-[12.5px] font-semibold",
            theme.textPrimary,
          )}
        >
          {name}
        </span>
        {hasPosition && (
          <Tag tone="outline">
            pos {chunk.position!.index} / {chunk.position!.total}
          </Tag>
        )}
        {hasScore && <Tag tone="brand">score {chunk.score!.toFixed(2)}</Tag>}
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-label={open ? `Hide chunk ${name}` : `View chunk ${name}`}
          className={cn(
            "inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs font-medium",
            theme.textMuted,
            theme.hoverBg,
          )}
        >
          {open ? "Hide" : "View"}
          <ChevronDown
            className={cn(
              "size-3.5 transition-transform",
              open && "rotate-180",
            )}
          />
        </button>
      </div>

      {open && (
        <div className="space-y-2.5 px-3 pb-3.5 pt-3">
          {chunk.metadata && (
            <div>
              <button
                type="button"
                onClick={() => setShowMeta((s) => !s)}
                aria-expanded={showMeta}
                className={cn(
                  "flex items-center gap-1.5 text-[11.5px] font-semibold",
                  theme.textMuted,
                )}
              >
                <Info className="size-3" />
                Chunk metadata
                <ChevronDown
                  className={cn(
                    "size-3 transition-transform",
                    showMeta && "rotate-180",
                  )}
                />
              </button>
              {showMeta && (
                <div
                  className={cn(
                    "mt-1.5 rounded-md border px-3.5 py-1",
                    theme.surfaceMuted,
                  )}
                >
                  <MetadataTable data={chunk.metadata} />
                </div>
              )}
            </div>
          )}
          <ChunkContent text={chunk.text} />
        </div>
      )}
    </div>
  );
};

export default ChunkRow;
