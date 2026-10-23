"use client";

import { useEffect, useMemo, useState } from "react";

import { isPlainObject } from "lodash-es";
import { Info } from "lucide-react";

import { cn } from "../utils/cn";
import { useSourcesTheme } from "./context";
import MetadataTable from "./MetadataTable";
import { SubHeader } from "./primitives";

interface DocumentMetadataTableProps {
  metadata?: Record<string, unknown>;
  forceOpen?: boolean;
  /** Initial open state when no expand-all override is active. */
  defaultOpen?: boolean;
}

const DocumentMetadataTable = ({
  metadata,
  forceOpen,
  defaultOpen = false,
}: DocumentMetadataTableProps) => {
  const theme = useSourcesTheme();
  const [open, setOpen] = useState(defaultOpen);

  useEffect(() => {
    if (forceOpen !== undefined) setOpen(forceOpen);
  }, [forceOpen]);

  const entries = useMemo(() => {
    if (!isPlainObject(metadata)) return [];
    return Object.entries(metadata as Record<string, unknown>).filter(
      ([, v]) => v !== null && v !== undefined && v !== "",
    );
  }, [metadata]);

  if (entries.length === 0) {
    return (
      <div
        className={cn(
          "flex items-center gap-2 rounded-lg border border-dashed px-3.5 py-2.5 text-[12.5px]",
          theme.emptyState,
        )}
      >
        <Info className="size-3.5 shrink-0" />
        No document metadata available for this source.
      </div>
    );
  }

  return (
    <div>
      <SubHeader
        icon={Info}
        iconChip={theme.subHeaderIconInfo}
        label="Document metadata"
        count={entries.length}
        open={open}
        onToggle={() => setOpen((o) => !o)}
      />
      {open && (
        <div
          className={cn(
            "mt-2 rounded-lg border px-3.5 py-3",
            theme.surfaceMuted,
          )}
        >
          <MetadataTable data={metadata} />
        </div>
      )}
    </div>
  );
};

export default DocumentMetadataTable;
