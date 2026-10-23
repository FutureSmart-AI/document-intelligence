"use client";

import { useEffect, useMemo, useState } from "react";

import {
  CODE,
  DATEISH,
  RESULT_PAGE_SIZE as PAGE_SIZE,
  UUID,
} from "./constants";

interface DiResultTableProps {
  /** Rows to draw — already narrowed by the card's filter box. */
  rows: Record<string, unknown>[];
  /** Visible columns, in order — the card owns which are hidden. */
  columns: string[];
  /** How many rows the result had before the filter, for the footer note. */
  totalRows: number;
  /** The card's filter box, for the "nothing matched" row. */
  query: string;
}

/** Cell text for any JSON value the frame can put in a column. */
export function display(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/**
 * Values that are read rather than spoken: ids, codes, dates, numbers.
 *
 * There is no per-column flag on the frame, so the column is judged by what is
 * actually in it. Prose — a name, a title, a snippet of a document — stays in
 * the body face.
 */

function isCodeColumn(rows: Record<string, unknown>[], key: string): boolean {
  const seen = rows
    .map((row) => row[key])
    .filter((value) => value !== null && value !== undefined && value !== "");
  if (!seen.length) return false;
  return seen.every((value) => {
    if (typeof value === "number") return true;
    if (typeof value !== "string") return false;
    return UUID.test(value) || DATEISH.test(value) || CODE.test(value);
  });
}

/** Keys greyed out: the row's identity and its timestamps. */
function isMutedColumn(key: string): boolean {
  return key === "id" || key.endsWith("_id") || key.endsWith("_at");
}

export default function DiResultTable({
  rows,
  columns,
  totalRows,
  query,
}: DiResultTableProps) {
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const [page, setPage] = useState(0);

  // A new result invalidates the page and the sort — leaving them would show
  // page 3 of a two-page answer, ordered by a column that no longer exists.
  useEffect(() => {
    setPage(0);
    setSort(null);
  }, [rows]);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const { key, dir } = sort;
    return [...rows].sort((a, b) => {
      const left = a[key];
      const right = b[key];
      if (typeof left === "number" && typeof right === "number") {
        return (left - right) * dir;
      }
      return (
        display(left).localeCompare(display(right), undefined, {
          numeric: true,
          sensitivity: "base",
        }) * dir
      );
    });
  }, [rows, sort]);

  const pages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const slice = sorted.slice(
    current * PAGE_SIZE,
    current * PAGE_SIZE + PAGE_SIZE,
  );

  const code = useMemo(
    () =>
      Object.fromEntries(columns.map((key) => [key, isCodeColumn(rows, key)])),
    [columns, rows],
  );

  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-line bg-sunken">
              {columns.map((key) => {
                const active = sort?.key === key;
                return (
                  <th
                    key={key}
                    scope="col"
                    className="px-3.5 py-[9px] text-left whitespace-nowrap"
                    aria-sort={
                      active
                        ? sort.dir === 1
                          ? "ascending"
                          : "descending"
                        : "none"
                    }
                  >
                    <button
                      type="button"
                      onClick={() =>
                        setSort((currentSort) =>
                          currentSort?.key === key
                            ? { key, dir: currentSort.dir === 1 ? -1 : 1 }
                            : { key, dir: 1 },
                        )
                      }
                      className={`inline-flex cursor-pointer items-center gap-1.5 border-0 bg-transparent p-0 text-[10.5px] font-bold tracking-[0.07em] uppercase ${
                        active ? "text-accent-strong" : "text-ink-3"
                      }`}
                    >
                      {key}
                      {/* A ghosted arrow stays on every column, so the header
                          reads as sortable before it is clicked. */}
                      <span
                        aria-hidden="true"
                        className={`text-[9px] ${active ? "opacity-100" : "opacity-25"}`}
                      >
                        {active && sort.dir === -1 ? "▼" : "▲"}
                      </span>
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {slice.map((row, index) => (
              <tr
                key={current * PAGE_SIZE + index}
                className="border-b border-line-soft hover:bg-sunken"
              >
                {columns.map((key) => (
                  <td
                    key={key}
                    className={`px-3.5 py-2.5 text-left text-[12.5px] whitespace-nowrap ${
                      code[key] ? "font-mono" : ""
                    } ${isMutedColumn(key) ? "text-ink-3" : "text-ink-2"}`}
                  >
                    {display(row[key])}
                  </td>
                ))}
              </tr>
            ))}
            {!slice.length && (
              <tr>
                <td
                  colSpan={Math.max(1, columns.length)}
                  className="px-3.5 py-6 text-center text-xs text-ink-3"
                >
                  No rows match “{query.trim()}”.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center gap-2 border-t border-line bg-sunken px-3 py-2">
        <span className="text-[11.5px] text-ink-3">
          {sorted.length
            ? `Showing ${current * PAGE_SIZE + 1}–${current * PAGE_SIZE + slice.length} of ${sorted.length.toLocaleString()}`
            : "No rows"}
          {sorted.length !== totalRows
            ? ` (filtered from ${totalRows.toLocaleString()})`
            : ""}
        </span>
        <div className="flex-1" />
        <button
          type="button"
          disabled={current === 0}
          onClick={() => setPage(current - 1)}
          className="cursor-pointer rounded-di-sm border border-line bg-surface px-2.5 py-1 text-[11.5px] font-semibold text-ink-2 hover:bg-sunken disabled:cursor-not-allowed disabled:opacity-40"
        >
          Previous
        </button>
        <button
          type="button"
          disabled={current >= pages - 1}
          onClick={() => setPage(current + 1)}
          className="cursor-pointer rounded-di-sm border border-line bg-surface px-2.5 py-1 text-[11.5px] font-semibold text-ink-2 hover:bg-sunken disabled:cursor-not-allowed disabled:opacity-40"
        >
          Next
        </button>
      </div>
    </>
  );
}
