"use client";

import React from "react";

import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn } from "@/platform/utils";

import { DEFAULT_PAGE_SIZE_OPTIONS } from "./constants";
import Select from "./Select";

export const PAGE_ELLIPSIS = "ellipsis" as const;
export type PageItem = number | typeof PAGE_ELLIPSIS;

/** Page buttons rendered at once, ellipsis slots included. */
const MAX_PAGE_BUTTONS = 7;

/**
 * Page buttons to render for a 0-based `pageIndex`, always keeping the first and
 * last page reachable and collapsing the gaps into ellipses:
 * `1 2 3 4 5 … 13`, `1 … 6 7 8 … 13`, `1 … 9 10 11 12 13`.
 */
export function buildPageItems(
  pageIndex: number,
  pageCount: number,
): PageItem[] {
  if (pageCount <= 0) return [];
  if (pageCount <= MAX_PAGE_BUTTONS) {
    return Array.from({ length: pageCount }, (_, i) => i);
  }

  const last = pageCount - 1;
  const current = Math.min(Math.max(pageIndex, 0), last);

  // Head window: 1 2 3 4 5 … last
  if (current <= 3) {
    return [0, 1, 2, 3, 4, PAGE_ELLIPSIS, last];
  }
  // Tail window: 1 … last-4 … last
  if (current >= last - 3) {
    return [0, PAGE_ELLIPSIS, last - 4, last - 3, last - 2, last - 1, last];
  }
  // Middle: 1 … current-1 current current+1 … last
  return [
    0,
    PAGE_ELLIPSIS,
    current - 1,
    current,
    current + 1,
    PAGE_ELLIPSIS,
    last,
  ];
}

export interface TablePaginationProps {
  /** 0-based index of the page on screen. */
  pageIndex: number;
  pageSize: number;
  /**
   * Total rows behind the pagination. Omit only when the total is genuinely
   * unknown (cursor/load-more APIs) — without it there are no page numbers to
   * jump to and the footer falls back to `Page N` plus prev/next.
   */
  totalItems?: number;
  /** Used instead of the computed page count when `totalItems` is unknown. */
  hasMore?: boolean;
  onPageChange: (pagination: { pageIndex: number; pageSize: number }) => void;
  pageSizeOptions?: number[];
  /** Hide the rows-per-page select (page size fixed by the caller). */
  showPageSize?: boolean;
  /** Blocks navigation while a page is in flight. */
  disabled?: boolean;
  className?: string;
}

const pageBtnBase =
  "inline-flex h-8 min-w-8 cursor-pointer items-center justify-center gap-1 rounded-md px-2 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40";

function PageButton({
  children,
  active,
  disabled,
  onClick,
  ariaLabel,
  ariaCurrent,
}: {
  children: React.ReactNode;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  ariaLabel?: string;
  ariaCurrent?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      aria-label={ariaLabel}
      aria-current={ariaCurrent ? "page" : undefined}
      className={cn(
        pageBtnBase,
        active
          ? "bg-indigo-600 text-white hover:bg-indigo-600"
          : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
      )}
    >
      {children}
    </button>
  );
}

/**
 * The single pagination footer every table view uses: rows-per-page select, the
 * `Showing 1–10 of 125` range, and Previous / numbered pages / Next. Page size
 * changes send the caller back to the first page; the caller keeps the chosen
 * size, so it survives navigating between pages.
 */
export function TablePagination({
  pageIndex,
  pageSize,
  totalItems,
  hasMore,
  onPageChange,
  pageSizeOptions = DEFAULT_PAGE_SIZE_OPTIONS,
  showPageSize = true,
  disabled = false,
  className,
}: TablePaginationProps) {
  const knowsTotal = totalItems !== undefined;
  const pageCount = knowsTotal
    ? Math.max(1, Math.ceil(totalItems / pageSize))
    : 0;

  const startItem =
    knowsTotal && totalItems === 0 ? 0 : pageIndex * pageSize + 1;
  const endItem = knowsTotal
    ? Math.min((pageIndex + 1) * pageSize, totalItems)
    : (pageIndex + 1) * pageSize;

  const canPrevious = pageIndex > 0;
  const canNext = knowsTotal ? pageIndex < pageCount - 1 : Boolean(hasMore);

  const goToPage = (nextIndex: number) => {
    const clamped = knowsTotal
      ? Math.min(Math.max(nextIndex, 0), pageCount - 1)
      : Math.max(nextIndex, 0);
    if (clamped === pageIndex) return;
    onPageChange({ pageIndex: clamped, pageSize });
  };

  const pageItems = knowsTotal ? buildPageItems(pageIndex, pageCount) : [];

  return (
    <nav
      aria-label="Pagination"
      className={cn(
        "flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-4 py-2.5",
        className,
      )}
    >
      <div className="flex min-w-0 items-center gap-2 text-xs text-slate-500">
        {showPageSize && (
          <label className="flex items-center gap-1.5 whitespace-nowrap">
            <span className="hidden sm:inline">Rows per page:</span>
            <span className="sm:hidden">Rows:</span>
            <Select
              aria-label="Rows per page"
              options={pageSizeOptions.map((size) => ({
                label: String(size),
                value: String(size),
              }))}
              value={String(pageSize)}
              // Page size changes reset to the first page — the old offset is
              // meaningless against the new page boundaries.
              onChange={(v) =>
                onPageChange({ pageIndex: 0, pageSize: parseInt(v, 10) })
              }
              size="xs"
              fullWidth={false}
              className="w-16"
              disabled={disabled}
            />
          </label>
        )}
        <span className="whitespace-nowrap">
          Showing {startItem.toLocaleString()}–{endItem.toLocaleString()}
          {knowsTotal && <> of {totalItems.toLocaleString()}</>}
        </span>
      </div>

      <div className="flex items-center gap-1">
        <PageButton
          disabled={disabled || !canPrevious}
          onClick={() => goToPage(pageIndex - 1)}
          ariaLabel="Previous page"
        >
          <ChevronLeft className="size-4" />
          <span className="hidden sm:inline">Previous</span>
        </PageButton>

        {knowsTotal ? (
          pageItems.map((item, i) =>
            item === PAGE_ELLIPSIS ? (
              <span
                key={`ellipsis-${i}`}
                aria-hidden="true"
                className="px-1 text-xs text-slate-400"
              >
                …
              </span>
            ) : (
              <PageButton
                key={item}
                active={item === pageIndex}
                ariaCurrent={item === pageIndex}
                disabled={disabled}
                onClick={() => goToPage(item)}
              >
                {item + 1}
              </PageButton>
            ),
          )
        ) : (
          <span className="px-2 text-xs text-slate-500">
            Page {pageIndex + 1}
          </span>
        )}

        <PageButton
          disabled={disabled || !canNext}
          onClick={() => goToPage(pageIndex + 1)}
          ariaLabel="Next page"
        >
          <span className="hidden sm:inline">Next</span>
          <ChevronRight className="size-4" />
        </PageButton>
      </div>
    </nav>
  );
}
