"use client";

import { useMemo, useState } from "react";

import {
  ColumnDef,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  SortingState,
  Updater,
  useReactTable,
  VisibilityState,
} from "@tanstack/react-table";
import { map } from "lodash-es";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";

import type { ColumnSchema } from "@/platform/types/data-view";
import { cn } from "@/platform/utils";

import { isColumnVisible } from "./columnVisibility";

interface DataTableProps {
  columns: ColumnSchema[];
  rows: Record<string, any>[];
  isLoading: boolean;
  isFetching?: boolean;
  onRowClick: (row: Record<string, any>) => void;
  /** Highlights the row currently shown in the preview panel. */
  isRowActive?: (row: Record<string, any>) => boolean;
  page: number;
  pageSize: number;
  sorting?: SortingState;
  onSortingChange?: (updater: Updater<SortingState>) => void;
}

export const DataTable = ({
  columns: apiColumns,
  rows,
  isLoading,
  isFetching,
  onRowClick,
  isRowActive,
  page = 1,
  pageSize = 10,
  sorting: externalSorting,
  onSortingChange: onExternalSortingChange,
}: DataTableProps) => {
  const [internalSorting, setInternalSorting] = useState<SortingState>([]);
  const sorting = externalSorting ?? internalSorting;
  const setSorting = onExternalSortingChange ?? setInternalSorting;

  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({});

  // Convert API columns to react-table column definitions
  const columns = useMemo<ColumnDef<Record<string, any>>[]>(() => {
    const dataColumns: ColumnDef<Record<string, any>>[] = apiColumns.map(
      (col) => ({
        accessorKey: col.name,
        id: col.name,
        size: getColumnWidth(col),
        minSize: 200,
        maxSize: 600,
        header: () => (
          <div className="flex items-center gap-1.5">
            <span className="truncate" title={formatColumnName(col.name)}>
              {formatColumnName(col.name)}
            </span>
            <span className="text-[9px] text-slate-400 font-normal px-1 py-0.5 rounded bg-slate-100 shrink-0 uppercase">
              {col.data_type || col.parent_type || "value"}
            </span>
          </div>
        ),
        cell: ({ getValue }) => {
          const value = getValue();
          return renderCellValue(value);
        },
        enableSorting: !!col.sort_field,
      }),
    );

    const serialNumberColumn: ColumnDef<Record<string, any>> = {
      id: "serial_number",
      header: () => "#",
      cell: ({ row }) => (
        <span className="text-slate-400">
          {(page - 1) * pageSize + row.index + 1}
        </span>
      ),
      enableSorting: false,
      size: 60,
      minSize: 60,
      maxSize: 60,
    };

    return [serialNumberColumn, ...dataColumns];
  }, [apiColumns, page, pageSize]);

  // Initialize visibility with key columns or API config. `isColumnVisible` is
  // shared with the CSV export so the file has the columns the user saw.
  useMemo(() => {
    const initialVisibility: VisibilityState = {};
    initialVisibility["serial_number"] = true;

    apiColumns.forEach((col) => {
      initialVisibility[col.name] = isColumnVisible(col);
    });
    setColumnVisibility(initialVisibility);
  }, [apiColumns]);

  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    manualSorting: Boolean(onExternalSortingChange),
    state: {
      sorting,
      columnVisibility,
    },
  });

  if (isLoading) {
    return (
      <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="bg-slate-50 border-b border-slate-200">
              {Array.from({ length: 5 }).map((_, i) => (
                <th key={i} className="px-4 py-2.5">
                  <div className="h-3 bg-slate-200 rounded animate-pulse w-20" />
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {Array.from({ length: 5 }).map((_, i) => (
              <tr key={i}>
                {Array.from({ length: 5 }).map((_, j) => (
                  <td key={j} className="px-4 py-2.5">
                    <div className="h-4 bg-slate-200 rounded animate-pulse" />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (apiColumns.length === 0) {
    return (
      <div className="bg-white rounded-lg border border-slate-200 px-4 py-10 text-center">
        <p className="text-sm text-slate-500">No data available</p>
      </div>
    );
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      {isFetching && (
        <div className="absolute inset-x-0 top-0 z-30 h-0.5 overflow-hidden rounded-t-lg bg-slate-200">
          <div className="h-full w-1/3 animate-[shimmer_1.5s_ease-in-out_infinite] bg-indigo-500 rounded-full" />
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-slate-200 bg-white">
        <div className="min-w-max">
          <table
            className="min-w-full table-fixed border-separate border-spacing-0"
            style={{ width: table.getTotalSize() }}
          >
            <thead className="bg-slate-50">
              {map(table.getHeaderGroups(), (headerGroup) => (
                <tr
                  key={headerGroup.id}
                  className="bg-slate-50 border-b border-slate-200"
                >
                  {map(headerGroup.headers, (header) => {
                    const canSort = header.column.getCanSort();
                    const sorted = header.column.getIsSorted();

                    return (
                      <th
                        key={header.id}
                        className={cn(
                          // Keep the header above cells within this table's
                          // own two-axis scroll viewport. Separate borders
                          // prevent body cells from painting through it.
                          "sticky top-0 z-20 border-b border-slate-200 bg-slate-50 px-4 py-2.5 text-left text-[11px] font-semibold text-slate-500 uppercase tracking-wider shadow-[0_1px_0_0_rgb(226_232_240)]",
                          canSort &&
                            "cursor-pointer select-none hover:bg-slate-100",
                        )}
                        style={{ width: header.getSize() }}
                        onClick={
                          canSort
                            ? header.column.getToggleSortingHandler()
                            : undefined
                        }
                      >
                        <div className="flex items-center gap-1">
                          {header.isPlaceholder
                            ? null
                            : flexRender(
                                header.column.columnDef.header,
                                header.getContext(),
                              )}
                          {canSort && (
                            <span className="ml-auto shrink-0">
                              {sorted === "asc" ? (
                                <ArrowUp className="size-3 text-indigo-600" />
                              ) : sorted === "desc" ? (
                                <ArrowDown className="size-3 text-indigo-600" />
                              ) : (
                                <ArrowUpDown className="size-3 text-slate-300" />
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
              {table.getRowModel().rows.length === 0 ? (
                <tr>
                  <td
                    colSpan={columns.length}
                    className="px-4 py-10 text-center"
                  >
                    <p className="text-sm text-slate-500">
                      No data found. Try adjusting your filters.
                    </p>
                  </td>
                </tr>
              ) : (
                map(table.getRowModel().rows, (row) => (
                  <tr
                    key={row.id}
                    aria-selected={
                      isRowActive
                        ? Boolean(isRowActive(row.original))
                        : undefined
                    }
                    onClick={() => onRowClick(row.original)}
                    className={cn(
                      "transition-colors cursor-pointer",
                      isRowActive?.(row.original)
                        ? "bg-indigo-50 ring-1 ring-inset ring-indigo-200"
                        : "hover:bg-slate-50",
                    )}
                  >
                    {map(row.getVisibleCells(), (cell) => (
                      <td
                        key={cell.id}
                        className="px-4 py-2.5 text-sm align-top"
                      >
                        <div
                          className="w-full"
                          style={{ maxWidth: cell.column.getSize() }}
                        >
                          {flexRender(
                            cell.column.columnDef.cell,
                            cell.getContext(),
                          )}
                        </div>
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

// Helper functions
function formatColumnName(name: string): string {
  return name
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function getColumnWidth(col: ColumnSchema): number {
  if (col.name === "object_id") return 120;
  if (col.name === "title") return 300;
  if (col.name === "summary") return 450;
  if (col.name === "document_type") return 140;
  if (col.name.includes("url") || col.name.includes("link")) return 250;
  if (col.name.includes("email")) return 200;

  switch (col.data_type) {
    case "int":
    case "float":
    case "bool":
      return 100;
    case "List[str]":
    case "List[dict]":
      return 300;
    case "str":
    default:
      return 200;
  }
}

function renderCellValue(value: any): React.ReactNode {
  if (value === null || value === undefined) {
    return <span className="text-slate-300">—</span>;
  }

  if (Array.isArray(value)) {
    if (value.length === 0) {
      return <span className="text-slate-300 italic text-xs">empty</span>;
    }
    return (
      <div className="flex flex-wrap gap-1">
        {value.slice(0, 3).map((item, idx) => (
          <span
            key={idx}
            className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-indigo-50 text-indigo-700 ring-1 ring-inset ring-indigo-200"
          >
            {String(item)}
          </span>
        ))}
        {value.length > 3 && (
          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-slate-50 text-slate-600 ring-1 ring-inset ring-slate-200">
            +{value.length - 3}
          </span>
        )}
      </div>
    );
  }

  if (typeof value === "object") {
    return (
      <span className="text-slate-600 font-mono text-xs bg-slate-50 px-1.5 py-0.5 rounded">
        {JSON.stringify(value)}
      </span>
    );
  }

  const strValue = String(value);
  if (strValue.length > 100) {
    return (
      <span className="text-slate-700 text-xs leading-relaxed" title={strValue}>
        {strValue.substring(0, 100)}...
      </span>
    );
  }

  return (
    <span className="text-slate-700 text-xs leading-relaxed">{strValue}</span>
  );
}

DataTable.displayName = "DataTable";
