"use client";

import {
  ChevronDown,
  Download,
  Maximize2,
  Minimize2,
  Search,
} from "lucide-react";

import { cn } from "../utils/cn";
import { useSourcesTheme } from "./context";
import { IconBtn } from "./primitives";

/** Which parts of each source card are shown. */
export type SourcesViewMode = "all" | "document" | "chunks";

const VIEW_MODE_OPTIONS: { value: SourcesViewMode; label: string }[] = [
  { value: "all", label: "All types" },
  { value: "document", label: "full_document" },
  { value: "chunks", label: "chunks_only" },
];

interface SourcesToolbarProps {
  query: string;
  onQueryChange: (q: string) => void;
  viewMode: SourcesViewMode;
  onViewModeChange: (m: SourcesViewMode) => void;
  allExpanded: boolean;
  onToggleExpandAll: () => void;
  onExport: () => void;
}

const SourcesToolbar = ({
  query,
  onQueryChange,
  viewMode,
  onViewModeChange,
  allExpanded,
  onToggleExpandAll,
  onExport,
}: SourcesToolbarProps) => {
  const theme = useSourcesTheme();

  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <div
        className={cn(
          "flex min-w-[150px] items-center gap-1.5 rounded-md border bg-white px-2 py-1",
          theme.control,
        )}
      >
        <Search className={cn("size-3 shrink-0", theme.textFaint)} />
        <input
          type="text"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder="Filter by file name"
          aria-label="Filter by file name"
          className={cn(
            "w-full bg-transparent text-[12px] focus:outline-none",
            theme.textBody,
          )}
        />
      </div>

      <div className="relative">
        <select
          value={viewMode}
          onChange={(e) => onViewModeChange(e.target.value as SourcesViewMode)}
          aria-label="Filter by view"
          className={cn(
            "appearance-none rounded-md border bg-white py-1 pl-2.5 pr-7 text-[12px] focus:outline-none",
            theme.control,
            theme.textBody,
            theme.controlFocus,
          )}
        >
          {VIEW_MODE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <ChevronDown
          className={cn(
            "pointer-events-none absolute right-2 top-1/2 size-3 -translate-y-1/2",
            theme.textMuted,
          )}
        />
      </div>

      <IconBtn icon={Download} onClick={onExport} label="Export sources" />
      <IconBtn
        icon={allExpanded ? Minimize2 : Maximize2}
        onClick={onToggleExpandAll}
        label={allExpanded ? "Collapse all" : "Expand all"}
        active={allExpanded}
      />
    </div>
  );
};

export default SourcesToolbar;
