"use client";

import { useEffect, useState } from "react";

import { Layers } from "lucide-react";

import type { SourceChunk } from "../types/source";
import { cn } from "../utils/cn";
import ChunkRow from "./ChunkRow";
import { useSourcesTheme } from "./context";
import { SubHeader } from "./primitives";

interface RelatedChunksListProps {
  chunks?: SourceChunk[];
  forceOpen?: boolean;
  /** Initial open state when no expand-all override is active. */
  defaultOpen?: boolean;
}

const RelatedChunksList = ({
  chunks,
  forceOpen,
  defaultOpen = false,
}: RelatedChunksListProps) => {
  const theme = useSourcesTheme();
  const [open, setOpen] = useState(defaultOpen);

  useEffect(() => {
    if (forceOpen !== undefined) setOpen(forceOpen);
  }, [forceOpen]);

  if (!chunks || chunks.length === 0) {
    return (
      <div
        className={cn(
          "flex items-center gap-2 rounded-lg border border-dashed px-3.5 py-2.5 text-[12.5px]",
          theme.emptyState,
        )}
      >
        <Layers className="size-3.5 shrink-0" />
        No related chunks — full document retrieval used for this source.
      </div>
    );
  }

  return (
    <div>
      <SubHeader
        icon={Layers}
        iconChip={theme.subHeaderIconChunks}
        label="Related chunks"
        count={chunks.length}
        open={open}
        onToggle={() => setOpen((o) => !o)}
      />
      {open && (
        <div className="mt-2 flex flex-col gap-2">
          {chunks.map((chunk, i) => (
            <ChunkRow
              key={chunk.chunk_id || `chunk_${i}`}
              chunk={chunk}
              forceOpen={forceOpen}
              defaultOpen={i === 0 && defaultOpen}
            />
          ))}
        </div>
      )}
    </div>
  );
};

export default RelatedChunksList;
