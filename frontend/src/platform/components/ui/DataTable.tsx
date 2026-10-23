"use client";

import React from "react";

import {
  ColumnDef,
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  OnChangeFn,
  SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { map } from "lodash-es";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Maximize2,
  Minimize2,
} from "lucide-react";

import { cn } from "@/platform/utils";

import Button from "./Button";
import { DEFAULT_PAGE_SIZE_OPTIONS } from "./constants";
import { TablePagination } from "./TablePagination";

// Extend ColumnDef meta to include alignment and row-click opt-out
declare module "@tanstack/react-table" {
  interface ColumnMeta<TData, TValue> {
    align?: "left" | "right" | "center";
    preventRowClick?: boolean;
    /** Pins the column to the right edge, so row actions stay reachable when
     * the table is narrowed (e.g. by an open preview panel) and scrolls. */
    stickyRight?: boolean;
  }
}

interface PaginationState {
  pageIndex: number;
  pageSize: number;
}

interface DataTableProps<TData, TValue> {
  columns: ColumnDef<TData, TValue>[];
  data: TData[];
  isLoading?: boolean;
  loadingRows?: number;
  emptyMessage?: string;
  emptyState?: React.ReactNode;
  onRowClick?: (row: TData) => void;
  /** Highlights the row currently shown in a side preview. */
  isRowActive?: (row: TData) => boolean;
  /** Rows in the order they are rendered, so callers can step through the list
   * exactly as the user sees it (sorting lives in here). Must be stable. */
  onVisibleRowsChange?: (rows: TData[]) => void;
  /** Every row in rendered sort order, pagination not applied — lets a caller
   * step a preview through the whole list and page the table to follow it.
   * Must be stable. */
  onSortedRowsChange?: (rows: TData[]) => void;
  enableSorting?: boolean;
  sorting?: SortingState;
  onSortingChange?: OnChangeFn<SortingState>;
  showPagination?: boolean;
  /**
   * `server` (default) — the caller slices `data` and owns page state.
   * `client` — the table holds every row and paginates in place, so sorting
   * applies across the whole list before it is cut into pages.
   */
  paginationMode?: "server" | "client";
  pagination?: PaginationState;
  onPaginationChange?: (pagination: PaginationState) => void;
  totalItems?: number;
  hasMore?: boolean;
  pageSizeOptions?: number[];
  /** Client pagination only: rows per page before the user picks a size. */
  defaultPageSize?: number;
  compact?: boolean;
  className?: string;
  enableFullscreen?: boolean;
  title?: string;
  subtitle?: string;
}

export function DataTable<TData, TValue>({
  columns,
  data,
  isLoading = false,
  loadingRows = 5,
  emptyMessage = "No data available",
  emptyState,
  onRowClick,
  isRowActive,
  onVisibleRowsChange,
  onSortedRowsChange,
  enableSorting = true,
  sorting: controlledSorting,
  onSortingChange: controlledOnSortingChange,
  showPagination = false,
  paginationMode = "server",
  pagination,
  onPaginationChange,
  totalItems: totalItemsProp,
  hasMore,
  pageSizeOptions = DEFAULT_PAGE_SIZE_OPTIONS,
  defaultPageSize = DEFAULT_PAGE_SIZE_OPTIONS[0],
  compact = false,
  className,
  enableFullscreen = false,
  title,
  subtitle,
}: DataTableProps<TData, TValue>) {
  const [internalSorting, setInternalSorting] = React.useState<SortingState>(
    [],
  );
  const [isFullscreen, setIsFullscreen] = React.useState(false);
  const [computedMaxHeight, setComputedMaxHeight] = React.useState<
    number | null
  >(null);
  const containerRef = React.useRef<HTMLDivElement>(null);

  // Measure distance from element's top to viewport bottom so siblings are accounted for.
  // Also re-runs on scroll so tables that start off-screen get correct height when scrolled into view.
  React.useLayoutEffect(() => {
    if (isFullscreen) return undefined;
    const el = containerRef.current;
    if (!el) return undefined;

    const compute = () => {
      const top = el.getBoundingClientRect().top;
      setComputedMaxHeight(Math.max(window.innerHeight - top - 16, 200));
    };

    const getScrollParent = (node: Element | null): Element | Window => {
      if (!node || node === document.documentElement) return window;
      const { overflow, overflowY } = getComputedStyle(node);
      if (
        overflow === "auto" ||
        overflow === "scroll" ||
        overflowY === "auto" ||
        overflowY === "scroll"
      )
        return node;
      return getScrollParent(node.parentElement);
    };

    const scrollParent = getScrollParent(el.parentElement);

    compute();
    window.addEventListener("resize", compute);
    scrollParent.addEventListener("scroll", compute);
    const ro = new ResizeObserver(compute);
    if (el.parentElement) ro.observe(el.parentElement);
    return () => {
      window.removeEventListener("resize", compute);
      scrollParent.removeEventListener("scroll", compute);
      ro.disconnect();
    };
  }, [isFullscreen]);

  // Handle Escape key to exit fullscreen
  React.useEffect(() => {
    if (!isFullscreen) return undefined;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsFullscreen(false);
    };
    document.addEventListener("keydown", handleKeyDown);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [isFullscreen]);

  const sorting = controlledSorting ?? internalSorting;
  const onSortingChange = controlledOnSortingChange ?? setInternalSorting;

  const isClientPaginated = showPagination && paginationMode === "client";
  // Client pagination is normally the table's own business, but a caller that
  // needs to drive the page (e.g. to reveal the row a side preview moved to)
  // can pass both `pagination` and `onPaginationChange` and own the state.
  const isPageStateControlled =
    isClientPaginated && pagination !== undefined && !!onPaginationChange;
  const [uncontrolledPagination, setUncontrolledPagination] =
    React.useState<PaginationState>({
      pageIndex: 0,
      pageSize: pagination?.pageSize ?? defaultPageSize,
    });
  const clientPagination = isPageStateControlled
    ? pagination
    : uncontrolledPagination;

  const setClientPagination = React.useCallback(
    (next: PaginationState) => {
      if (!isPageStateControlled) setUncontrolledPagination(next);
      onPaginationChange?.(next);
    },
    [isPageStateControlled, onPaginationChange],
  );

  // Filters shrink or grow the dataset and sorting reorders it — either way the
  // old offset points at unrelated rows, so both send the user back to page 1.
  // Auto-reset is off so a background refetch that returns the same rows does
  // not yank the page out from under them.
  const sortingKey = sorting.map((s) => `${s.id}:${s.desc}`).join(",");
  const pageIndexRef = React.useRef(clientPagination.pageIndex);
  pageIndexRef.current = clientPagination.pageIndex;
  const pageSizeRef = React.useRef(clientPagination.pageSize);
  pageSizeRef.current = clientPagination.pageSize;
  React.useEffect(() => {
    if (!isClientPaginated || pageIndexRef.current === 0) return;
    setClientPagination({ pageIndex: 0, pageSize: pageSizeRef.current });
  }, [isClientPaginated, data.length, sortingKey, setClientPagination]);

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    state: {
      ...(enableSorting ? { sorting } : {}),
      ...(isClientPaginated ? { pagination: clientPagination } : {}),
    },
    ...(enableSorting
      ? {
          getSortedRowModel: getSortedRowModel(),
          onSortingChange,
          enableSortingRemoval: true,
        }
      : {}),
    ...(isClientPaginated
      ? {
          getPaginationRowModel: getPaginationRowModel(),
          onPaginationChange: (updater) =>
            setClientPagination(
              typeof updater === "function"
                ? updater(clientPagination)
                : updater,
            ),
          autoResetPageIndex: false,
        }
      : {}),
  });

  // Publish the rendered order (post-sort) so a side panel can step through the
  // list the user actually sees. Keyed on row ids because `getRowModel()`
  // returns a fresh array on every render.
  const renderedRows = table.getRowModel().rows;
  const renderedRowKey = renderedRows.map((row) => row.id).join("|");
  React.useEffect(() => {
    onVisibleRowsChange?.(renderedRows.map((row) => row.original));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- renderedRowKey stands in for renderedRows, whose identity changes every render.
  }, [renderedRowKey, onVisibleRowsChange]);

  // Same, but the whole sorted list rather than the page on screen — a preview
  // stepping past the last row of a page needs the row that follows it.
  const sortedRows = enableSorting
    ? table.getSortedRowModel().rows
    : table.getCoreRowModel().rows;
  const sortedRowKey = sortedRows.map((row) => row.id).join("|");
  React.useEffect(() => {
    onSortedRowsChange?.(sortedRows.map((row) => row.original));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- sortedRowKey stands in for sortedRows, whose identity changes every render.
  }, [sortedRowKey, onSortedRowsChange]);

  // Pagination helpers. Client mode knows the whole dataset, so it always has a
  // real total; server mode only has what the caller passes down.
  const pageIndex = isClientPaginated
    ? clientPagination.pageIndex
    : (pagination?.pageIndex ?? 0);
  const pageSize = isClientPaginated
    ? clientPagination.pageSize
    : (pagination?.pageSize ?? 50);
  const totalItems = isClientPaginated
    ? data.length
    : (totalItemsProp ?? data.length);

  const handlePaginationChange = (next: PaginationState) => {
    if (isClientPaginated) setClientPagination(next);
    onPaginationChange?.(next);
  };

  const headerPadding = compact ? "px-3 py-2" : "px-4 py-2.5";
  const cellPadding = compact ? "px-3 py-1.5" : "px-4 py-2.5";
  const cellText = compact ? "text-xs" : "text-sm";

  const fullscreenButton = enableFullscreen && (
    <Button
      variant="ghost"
      onClick={() => setIsFullscreen((prev) => !prev)}
      title={isFullscreen ? "Exit fullscreen" : "Fullscreen view"}
    >
      {isFullscreen ? (
        <Minimize2 className="w-4 h-4" />
      ) : (
        <Maximize2 className="w-4 h-4" />
      )}
    </Button>
  );

  const tableContent = (
    <div
      ref={containerRef}
      className={cn(
        "bg-white rounded-lg border border-slate-200 overflow-hidden flex flex-col",
        isFullscreen ? "h-full" : "flex-1 min-h-0",
        !isFullscreen && className,
      )}
      style={
        !isFullscreen
          ? {
              maxHeight: computedMaxHeight
                ? `${computedMaxHeight}px`
                : "calc(100vh - 3.5rem)",
            }
          : undefined
      }
    >
      {/* Header bar with title/subtitle and fullscreen toggle */}
      {(title || subtitle || enableFullscreen) && (
        <div className="shrink-0 flex items-center justify-between px-4 py-2.5 border-b border-slate-200 bg-slate-50/50">
          <div className="min-w-0">
            {title && (
              <span className="text-sm font-medium text-slate-700">
                {title}
              </span>
            )}
            {subtitle && (
              <p className="text-xs text-slate-500 mt-0.5 truncate">
                {subtitle}
              </p>
            )}
          </div>
          {enableFullscreen && (
            <div className="flex items-center gap-1 shrink-0 ml-2">
              {fullscreenButton}
            </div>
          )}
        </div>
      )}
      <div className="overflow-auto flex-1 min-h-0 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-slate-300 [&::-webkit-scrollbar-track]:bg-transparent">
        <table className="w-full">
          <thead className="sticky top-0 z-10">
            {map(table.getHeaderGroups(), (headerGroup) => (
              <tr
                key={headerGroup.id}
                className="bg-slate-50 border-b border-slate-200"
              >
                {map(headerGroup.headers, (header) => {
                  const canSort = enableSorting && header.column.getCanSort();
                  const sorted = header.column.getIsSorted();
                  const align = header.column.columnDef.meta?.align;

                  return (
                    <th
                      key={header.id}
                      className={cn(
                        headerPadding,
                        "text-[11px] font-semibold text-slate-500 uppercase tracking-wider group/th",
                        align === "right"
                          ? "text-right"
                          : align === "center"
                            ? "text-center"
                            : "text-left",
                        canSort &&
                          "cursor-pointer select-none hover:bg-slate-100 hover:text-slate-700 transition-colors",
                        sorted && "text-indigo-600 bg-indigo-50/50",
                        header.column.columnDef.meta?.stickyRight &&
                          "sticky right-0 z-20 bg-slate-50",
                      )}
                      onClick={
                        canSort
                          ? header.column.getToggleSortingHandler()
                          : undefined
                      }
                    >
                      <div
                        className={cn(
                          "flex items-center gap-1",
                          align === "right" && "justify-end",
                          align === "center" && "justify-center",
                        )}
                      >
                        {header.isPlaceholder
                          ? null
                          : flexRender(
                              header.column.columnDef.header,
                              header.getContext(),
                            )}
                        {canSort && (
                          <span
                            className={cn(
                              "ml-1 transition-colors",
                              sorted
                                ? "text-indigo-600"
                                : "text-slate-400 opacity-0 group-hover/th:opacity-100",
                            )}
                          >
                            {sorted === "asc" ? (
                              <ArrowUp className="w-3 h-3" />
                            ) : sorted === "desc" ? (
                              <ArrowDown className="w-3 h-3" />
                            ) : (
                              <ArrowUpDown className="w-3 h-3" />
                            )}
                          </span>
                        )}
                      </div>
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading ? (
              Array.from({ length: loadingRows }).map((_, i) => (
                <tr
                  key={i}
                  className={i % 2 === 0 ? "bg-white" : "bg-slate-50/30"}
                >
                  {columns.map((col, colIdx) => {
                    const align = (col as any).meta?.align;
                    const widthPercent = 40 + ((colIdx * 17 + i * 13) % 45);
                    return (
                      <td key={colIdx} className={cellPadding}>
                        <div
                          className={cn(
                            "flex",
                            align === "right" && "justify-end",
                          )}
                        >
                          <div
                            className="h-4 bg-slate-200 rounded-md animate-pulse"
                            style={{
                              width: `${widthPercent}%`,
                              animationDelay: `${i * 100 + colIdx * 50}ms`,
                            }}
                          />
                        </div>
                      </td>
                    );
                  })}
                </tr>
              ))
            ) : table.getRowModel().rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="px-4 py-10 text-center">
                  {emptyState || (
                    <p className="text-sm text-slate-500">{emptyMessage}</p>
                  )}
                </td>
              </tr>
            ) : (
              map(table.getRowModel().rows, (row) => {
                const isActive = isRowActive?.(row.original);
                return (
                  <tr
                    key={row.id}
                    aria-selected={isRowActive ? Boolean(isActive) : undefined}
                    className={cn(
                      "group/row transition-colors",
                      isActive
                        ? "bg-indigo-50 ring-1 ring-inset ring-indigo-200"
                        : "bg-white hover:bg-slate-50",
                      onRowClick && "cursor-pointer",
                    )}
                    onClick={
                      onRowClick ? () => onRowClick(row.original) : undefined
                    }
                  >
                    {map(row.getVisibleCells(), (cell) => {
                      const align = cell.column.columnDef.meta?.align;
                      const shouldPreventRowClick =
                        onRowClick &&
                        cell.column.columnDef.meta?.preventRowClick;
                      return (
                        <td
                          key={cell.id}
                          className={cn(
                            cellPadding,
                            cellText,
                            align === "right"
                              ? "text-right"
                              : align === "center"
                                ? "text-center"
                                : undefined,
                            // Pinned actions keep the row's own background so
                            // the scrolled-under cells don't show through.
                            cell.column.columnDef.meta?.stickyRight &&
                              cn(
                                "sticky right-0 z-10 shadow-[-8px_0_8px_-8px_rgba(15,23,42,0.12)]",
                                isActive
                                  ? "bg-indigo-50"
                                  : "bg-white group-hover/row:bg-slate-50",
                              ),
                          )}
                          onClick={
                            shouldPreventRowClick
                              ? (e) => e.stopPropagation()
                              : undefined
                          }
                        >
                          {flexRender(
                            cell.column.columnDef.cell,
                            cell.getContext(),
                          )}
                        </td>
                      );
                    })}
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {showPagination &&
        (isClientPaginated || onPaginationChange) &&
        !isLoading &&
        data.length > 0 && (
          <TablePagination
            pageIndex={pageIndex}
            pageSize={pageSize}
            totalItems={
              isClientPaginated || totalItemsProp !== undefined
                ? totalItems
                : undefined
            }
            hasMore={hasMore}
            onPageChange={handlePaginationChange}
            pageSizeOptions={pageSizeOptions}
            className="shrink-0 border-t border-slate-200 bg-slate-50"
          />
        )}
    </div>
  );

  if (isFullscreen) {
    return (
      <div className="fixed inset-0 z-50 bg-white flex flex-col animate-in fade-in duration-200">
        {tableContent}
      </div>
    );
  }

  return tableContent;
}
