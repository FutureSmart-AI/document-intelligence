"use client";

import Dropdown from "@/components/Dropdown";
import Icon, { type IconName } from "@/components/Icon";
import { Btn, ToggleRow } from "@/components/ui";
import { cn } from "@/platform/utils";

import ColumnsPanel from "./ColumnsPanel";
import { ADDED_VIA_OPTIONS, COLUMN_GROUPS } from "./constants";
import StatusStrip from "./StatusStrip";
import type { ColumnGroupKey, StatusCounts, TableColumn } from "./types";

/**
 * Two tiers, on purpose.
 *
 * Fourteen controls will not fit one bar at 1000px, and left to wrap wherever
 * they run out of room the grouping changes as the window resizes. So the bar
 * breaks in a fixed place: tier one narrows which **rows** are shown (search,
 * status, type), tier two shapes the **table** and acts on it (show, columns,
 * filters, sort, group & chart, density, full screen, refresh, export).
 */

function ToolButton({
  icon,
  label,
  demo,
  badge,
  on,
  onClick,
  title,
  ariaExpanded,
}: {
  icon: IconName;
  label: string;
  /**
   * A count the control is carrying. A number is only drawn when it is above
   * zero — a "0" badge claims a filter is on; `Columns` passes the string
   * `"n/total"`, which is always worth stating.
   */
  badge?: number | string;
  /** The anchor a recorded demo drives this control by. */
  demo?: string;
  /** A control that is doing something has to look like it — a badge is missable. */
  on?: boolean;
  onClick: () => void;
  title?: string;
  ariaExpanded?: boolean;
}) {
  const showBadge = typeof badge === "string" ? !!badge : (badge ?? 0) > 0;
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-expanded={ariaExpanded}
      data-demo={demo}
      className={cn(
        "inline-flex cursor-pointer items-center gap-1.5 rounded-di-md border px-2.5 py-1.75",
        "text-[13px] font-semibold whitespace-nowrap",
        on
          ? "border-accent bg-accent-soft text-accent"
          : "border-line-strong bg-surface text-ink hover:border-accent hover:bg-sunken",
      )}
    >
      <Icon name={icon} className="size-3.75" />
      {label}
      {showBadge && (
        <span className="tnum inline-flex h-3.75 items-center rounded-full bg-accent px-1.25 text-[10.5px] text-accent-contrast">
          {badge}
        </span>
      )}
    </button>
  );
}

function IconToggle({
  icon,
  label,
  on,
  onClick,
  demo,
}: {
  icon: IconName;
  label: string;
  on: boolean;
  onClick: () => void;
  demo?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      aria-pressed={on}
      data-demo={demo}
      className={cn(
        "inline-flex size-8 cursor-pointer items-center justify-center rounded-di-md border",
        on
          ? "border-accent bg-accent-soft text-accent"
          : "border-line-strong bg-surface text-ink-2 hover:bg-sunken hover:text-ink",
      )}
    >
      <Icon name={icon} className="size-4" />
    </button>
  );
}

/** All / Status only / Fields only, plus a switch per group. */
function focusLabel(groups: Record<ColumnGroupKey, boolean>): string {
  if (groups.status && groups.fields) return "All";
  if (groups.status) return "Status only";
  if (groups.fields) return "Fields only";
  return "Custom";
}

export default function KnowledgeToolbar({
  search,
  onSearchChange,
  counts,
  status,
  onStatusChange,
  type,
  onTypeChange,
  groups,
  onToggleGroup,
  onGroupPreset,
  columns,
  hidden,
  visibleCount,
  onSetHidden,
  onSetMany,
  filterCount,
  filtersOpen,
  onToggleFilters,
  sortCount,
  sortOpen,
  onToggleSort,
  facetOpen,
  onToggleFacet,
  compact,
  onToggleCompact,
  fullScreen,
  onToggleFullScreen,
  isFetching,
  onRefresh,
  onExportVisible,
  onExportAll,
}: {
  search: string;
  onSearchChange: (value: string) => void;
  counts: StatusCounts;
  status: string;
  onStatusChange: (value: string) => void;
  type: string;
  onTypeChange: (value: string) => void;
  groups: Record<ColumnGroupKey, boolean>;
  onToggleGroup: (group: ColumnGroupKey) => void;
  onGroupPreset: (preset: "all" | "status" | "fields") => void;
  columns: TableColumn[];
  hidden: Record<string, boolean>;
  visibleCount: number;
  onSetHidden: (key: string, value: boolean) => void;
  onSetMany: (keys: string[], value: boolean) => void;
  filterCount: number;
  filtersOpen: boolean;
  onToggleFilters: () => void;
  sortCount: number;
  sortOpen: boolean;
  onToggleSort: () => void;
  facetOpen: boolean;
  onToggleFacet: () => void;
  compact: boolean;
  onToggleCompact: () => void;
  fullScreen: boolean;
  onToggleFullScreen: () => void;
  isFetching: boolean;
  onRefresh: () => void;
  onExportVisible: () => void;
  onExportAll: () => void;
}) {
  return (
    <div
      data-demo="knowledge-toolbar"
      className="mb-3 flex flex-wrap items-center gap-2 gap-y-2.5"
    >
      <div className="flex min-w-50 flex-1 basis-60 items-center gap-1.5 rounded-di-md border border-line bg-sunken px-2.5 py-1.5 text-ink-3">
        <Icon name="search" className="size-3.5" />
        <input
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Search a name, an object ID, or any extracted value"
          aria-label="Search documents"
          data-demo="knowledge-search"
          className="w-full border-0 bg-transparent text-[13px] text-ink outline-none"
        />
      </div>

      <StatusStrip counts={counts} value={status} onChange={onStatusChange} />

      <span className="h-5.5 w-px flex-none bg-line" aria-hidden="true" />

      <Dropdown
        panelClassName="w-44"
        trigger={({ open, toggle }) => (
          <button
            type="button"
            onClick={toggle}
            aria-expanded={open}
            data-demo="knowledge-type"
            className="inline-flex cursor-pointer items-center gap-1.5 rounded-di-md border border-line-strong bg-surface px-2.5 py-1.5 text-[12.5px] text-ink hover:bg-sunken"
          >
            {type === "all" ? "All types" : type}
            <Icon name="caret" className="size-3.5 text-ink-3" />
          </button>
        )}
      >
        {({ close }) => (
          <>
            {["all", ...ADDED_VIA_OPTIONS].map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => {
                  onTypeChange(option);
                  close();
                }}
                className={cn(
                  "block w-full cursor-pointer rounded-di-sm px-2.25 py-1.75 text-left text-[12.5px]",
                  option === type
                    ? "bg-accent-soft font-semibold text-accent-softtext"
                    : "text-ink hover:bg-sunken",
                )}
              >
                {option === "all" ? "All types" : option}
              </button>
            ))}
          </>
        )}
      </Dropdown>

      {/* The hard break between the two tiers, so the grouping is the same at
          every width instead of reshuffling as the window resizes. */}
      <span className="h-0 basis-full" aria-hidden="true" />

      <Dropdown
        panelClassName="w-70"
        trigger={({ open, toggle }) => (
          <button
            type="button"
            onClick={toggle}
            aria-expanded={open}
            data-demo="knowledge-show"
            className="inline-flex cursor-pointer items-center gap-1.5 rounded-di-md border border-line-strong bg-surface px-2.5 py-1.5 text-[12.5px] text-ink hover:bg-sunken"
          >
            Show: {focusLabel(groups)}
            <Icon name="caret" className="size-3.5 text-ink-3" />
          </button>
        )}
      >
        {() => (
          <>
            <p className="mb-2.5 text-[11.5px] leading-[1.5] text-ink-2">
              Fewer columns, same rows. This is the quick axis —{" "}
              <b className="text-ink">Columns</b> is the fine one, and it keeps
              its own choices while a group is hidden.
            </p>
            <div className="mb-2.5 inline-flex overflow-hidden rounded-di-md border border-line-strong">
              {(
                [
                  { key: "all", label: "All" },
                  { key: "status", label: "Status only" },
                  { key: "fields", label: "Fields only" },
                ] as const
              ).map((preset) => (
                <button
                  key={preset.key}
                  type="button"
                  onClick={() => onGroupPreset(preset.key)}
                  aria-pressed={focusLabel(groups) === preset.label}
                  className={cn(
                    "cursor-pointer px-2.5 py-1.5 text-[12px] font-semibold",
                    focusLabel(groups) === preset.label
                      ? "bg-accent-soft text-accent-softtext"
                      : "bg-surface text-ink-2 hover:bg-sunken",
                  )}
                >
                  {preset.label}
                </button>
              ))}
            </div>
            {COLUMN_GROUPS.map((group) => (
              <ToggleRow
                key={group.key}
                label={group.label}
                desc={group.note}
                checked={groups[group.key]}
                disabled={group.locked}
                onChange={() => onToggleGroup(group.key)}
              />
            ))}
          </>
        )}
      </Dropdown>

      <Dropdown
        panelClassName="p-2.5"
        trigger={({ open, toggle }) => (
          <ToolButton
            icon="cols"
            label="Columns"
            demo="knowledge-columns"
            on={visibleCount < columns.length}
            // Always stated, not only when some are hidden: "17/17" is how you
            // know nothing is missing before reading a value you cannot find.
            badge={`${visibleCount}/${columns.length}`}
            ariaExpanded={open}
            onClick={toggle}
            title="Choose which columns are on screen"
          />
        )}
      >
        {({ close }) => (
          <ColumnsPanel
            columns={columns}
            hidden={hidden}
            onSetHidden={onSetHidden}
            onSetMany={onSetMany}
            onDone={close}
          />
        )}
      </Dropdown>

      <ToolButton
        icon="filter"
        label="Filters"
        demo="knowledge-filters"
        badge={filterCount}
        on={filterCount > 0}
        ariaExpanded={filtersOpen}
        onClick={onToggleFilters}
        title="Keep only the documents that match your rules"
      />
      <ToolButton
        icon="sort"
        label="Sort"
        demo="knowledge-sort"
        badge={sortCount}
        on={sortCount > 0}
        ariaExpanded={sortOpen}
        onClick={onToggleSort}
        title="Order the rows by one column or several"
      />
      <ToolButton
        icon="chart"
        label="Group & chart"
        demo="knowledge-group-chart"
        // The rules a value clicked in the band writes: the band and the
        // Filters panel are the same filter, so they carry the same count.
        badge={filterCount}
        on={facetOpen}
        ariaExpanded={facetOpen}
        onClick={onToggleFacet}
        title="Group the documents by a column and chart it"
      />

      <span className="h-5.5 w-px flex-none bg-line" aria-hidden="true" />

      <IconToggle
        icon="rows"
        demo="knowledge-density"
        label={
          compact ? "Go back to roomy rows" : "Fit more rows on the screen"
        }
        on={compact}
        onClick={onToggleCompact}
      />
      {/* The way out sits where the way in was, rather than on a strip of its
          own above the table — a row that exists only to hold one button is
          page the table was just given back. Named while it is on, because a
          lit ✕ beside the other toggles reads as "close" something. */}
      {fullScreen ? (
        <Btn
          size="sm"
          // Filled in grey, darker than the surface it sits on and a shade
          // darker again on hover: it is the toggle that is currently on,
          // standing in for a pressed icon toggle, so it has to read as held
          // down at a glance rather than as one more action waiting to be
          // taken. Both fills are tokens, so dark mode greys rather than
          // washes out.
          className="border-ink-3/40 bg-line-strong text-ink hover:bg-ink-3/35"
          data-demo="knowledge-fullscreen-exit"
          aria-pressed
          title="Give the page back to the rest of the screen"
          onClick={onToggleFullScreen}
          startIcon={<Icon name="x" className="size-3.5" />}
        >
          Exit full screen
        </Btn>
      ) : (
        <IconToggle
          icon="expand"
          demo="knowledge-fullscreen"
          label="Give the table the whole page"
          on={false}
          onClick={onToggleFullScreen}
        />
      )}

      <Btn
        size="sm"
        data-demo="knowledge-refresh"
        onClick={onRefresh}
        disabled={isFetching}
        startIcon={
          <Icon
            name="ref"
            className={cn("size-3.5", isFetching && "animate-spin")}
          />
        }
      >
        Refresh
      </Btn>

      <Dropdown
        align="right"
        panelClassName="w-62"
        trigger={({ open, toggle }) => (
          <Btn
            size="sm"
            data-demo="knowledge-export"
            onClick={toggle}
            aria-expanded={open}
            startIcon={<Icon name="dn" className="size-3.5" />}
          >
            Export
            <Icon name="caret" className="size-3.5 text-ink-3" />
          </Btn>
        )}
      >
        {({ close }) => (
          <>
            <button
              type="button"
              onClick={() => {
                onExportVisible();
                close();
              }}
              className="flex w-full cursor-pointer flex-col gap-0.5 rounded-di-sm px-2.25 py-2 text-left hover:bg-sunken"
            >
              <b className="text-[12.5px] text-ink">
                Export what I am looking at
              </b>
              <span className="text-[11.5px] text-ink-2">
                The rows and columns on screen now, as CSV.
              </span>
            </button>
            <button
              type="button"
              data-demo="knowledge-export-all"
              onClick={() => {
                onExportAll();
                close();
              }}
              className="flex w-full cursor-pointer flex-col gap-0.5 rounded-di-sm px-2.25 py-2 text-left hover:bg-sunken"
            >
              <b className="text-[12.5px] text-ink">Export everything</b>
              <span className="text-[11.5px] text-ink-2">
                Every document and every column, as CSV.
              </span>
            </button>
          </>
        )}
      </Dropdown>
    </div>
  );
}
