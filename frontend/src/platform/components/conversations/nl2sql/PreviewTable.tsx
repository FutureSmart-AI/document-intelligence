"use client";

import { useEffect, useMemo, useState } from "react";

import { ColumnDef, OnChangeFn, SortingState } from "@tanstack/react-table";
import { X } from "lucide-react";

import { DataTable } from "@/platform/components/ui/DataTable";
import Input from "@/platform/components/ui/Input";
import Select from "@/platform/components/ui/Select";

interface PreviewTableProps {
  rows: Record<string, unknown>[];
  pageSize?: number;
  emptyMessage?: string;
}

export const PreviewTable = ({
  rows,
  pageSize = 10,
  emptyMessage = "No preview rows available",
}: PreviewTableProps) => {
  const [filter, setFilter] = useState("");
  const [filterCol, setFilterCol] = useState<string>("__all__");
  const [pageIndex, setPageIndex] = useState(0);
  const [currentPageSize, setCurrentPageSize] = useState(pageSize);
  const [sorting, setSorting] = useState<SortingState>([]);

  useEffect(() => {
    setPageIndex(0);
  }, [rows]);

  useEffect(() => {
    setCurrentPageSize(pageSize);
  }, [pageSize]);

  const colKeys = useMemo(() => {
    if (rows.length === 0) return [];
    const keySet = new Set<string>();
    for (const row of rows) {
      for (const key of Object.keys(row)) keySet.add(key);
    }
    return Array.from(keySet);
  }, [rows]);

  const colOptions = useMemo(
    () => [
      { label: "All columns", value: "__all__" },
      ...colKeys.map((k) => ({ label: k, value: k })),
    ],
    [colKeys],
  );

  const columns = useMemo<ColumnDef<Record<string, unknown>>[]>(() => {
    return colKeys.map((key) => ({
      accessorKey: key,
      header: key,
      cell: (info) => {
        const v = info.getValue();
        const raw =
          v !== null && typeof v === "object"
            ? JSON.stringify(v)
            : String(v ?? "");

        const q = filter.trim();
        const shouldHighlight =
          q !== "" && (filterCol === "__all__" || filterCol === key);

        if (shouldHighlight) {
          const idx = raw.toLowerCase().indexOf(q.toLowerCase());
          if (idx !== -1) {
            const before = raw.slice(0, idx);
            const match = raw.slice(idx, idx + q.length);
            const after = raw.slice(idx + q.length);
            return (
              <span>
                {before}
                <mark className="bg-yellow-200 rounded-sm text-slate-900">
                  {match}
                </mark>
                {after}
              </span>
            );
          }
        }

        return raw;
      },
    }));
  }, [colKeys, filter, filterCol]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((row) => {
      if (filterCol === "__all__") {
        return Object.values(row).some((v) => {
          const str =
            v !== null && typeof v === "object"
              ? JSON.stringify(v)
              : String(v ?? "");
          return str.toLowerCase().includes(q);
        });
      } else {
        const v = row[filterCol];
        const str =
          v !== null && typeof v === "object"
            ? JSON.stringify(v)
            : String(v ?? "");
        return str.toLowerCase().includes(q);
      }
    });
  }, [rows, filter, filterCol]);

  const sorted = useMemo(() => {
    if (sorting.length === 0) return filtered;
    const { id, desc } = sorting[0];
    return [...filtered].sort((a, b) => {
      const av = a[id];
      const bv = b[id];
      let cmp: number;
      if (typeof av === "number" && typeof bv === "number") {
        cmp = av - bv;
      } else {
        const aStr = String(av ?? "");
        const bStr = String(bv ?? "");
        const aNum = Number(aStr.replace(/,/g, ""));
        const bNum = Number(bStr.replace(/,/g, ""));
        if (
          !Number.isNaN(aNum) &&
          !Number.isNaN(bNum) &&
          aStr !== "" &&
          bStr !== ""
        ) {
          cmp = aNum - bNum;
        } else {
          cmp = aStr.localeCompare(bStr, undefined, {
            numeric: true,
            sensitivity: "base",
          });
        }
      }
      return desc ? -cmp : cmp;
    });
  }, [filtered, sorting]);

  const pageRows = useMemo(
    () =>
      sorted.slice(
        pageIndex * currentPageSize,
        pageIndex * currentPageSize + currentPageSize,
      ),
    [sorted, pageIndex, currentPageSize],
  );

  if (rows.length === 0) {
    return (
      <div className="text-xs text-slate-500 py-4 text-center">
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Select
          options={colOptions}
          value={filterCol}
          onChange={(v) => {
            setFilterCol(v);
            setPageIndex(0);
          }}
          size="sm"
          fullWidth={false}
        />
        <div className="relative">
          <Input
            value={filter}
            onChange={(value) => {
              setFilter(value);
              setPageIndex(0);
            }}
            placeholder="Filter rows..."
            className={filter !== "" ? "pr-7" : ""}
          />
          {filter !== "" && (
            <button
              type="button"
              aria-label="Clear filter"
              onClick={() => {
                setFilter("");
                setFilterCol("__all__");
                setPageIndex(0);
              }}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
            >
              <X size={14} aria-hidden="true" />
            </button>
          )}
        </div>
      </div>
      {filter.trim() !== "" && (
        <p className="text-xs text-slate-500">
          Showing {filtered.length.toLocaleString()} of{" "}
          {rows.length.toLocaleString()} rows
        </p>
      )}
      <DataTable
        columns={columns}
        data={pageRows}
        enableSorting
        sorting={sorting}
        onSortingChange={setSorting as OnChangeFn<SortingState>}
        showPagination
        compact
        totalItems={filtered.length}
        pagination={{ pageIndex, pageSize: currentPageSize }}
        onPaginationChange={(p) => {
          setPageIndex(p.pageIndex);
          setCurrentPageSize(p.pageSize);
        }}
        emptyMessage="No matching rows"
      />
    </div>
  );
};

export default PreviewTable;
