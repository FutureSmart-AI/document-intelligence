"use client";

import { useMemo, useState } from "react";

import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";

import { useAuth } from "@/platform/hooks/useAuth";
import { getDataViewTabular } from "@/platform/services/data-view";
import type { ColumnSchema } from "@/platform/types/data-view";
import { cn } from "@/platform/utils";

// Scalar fields that are unique per document or too long to group meaningfully
const EXCLUDED_ORIGINAL_FIELDS = new Set([
  "object_id",
  "summary",
  "document_type",
  "email",
  "phone",
]);

// Preferred context columns shown alongside the active field in the doc table
const CONTEXT_COLUMNS: { key: string; label: string }[] = [
  { key: "current_role", label: "Role" },
  { key: "address_city", label: "City" },
  { key: "education_0_degree", label: "Education" },
];

interface FieldDef {
  key: string;
  label: string;
  columnNames: string[];
  extractFn: (row: Record<string, any>) => string[];
}

interface ValueCount {
  value: string;
  count: number;
}

interface FetchedData {
  columns: ColumnSchema[];
  rows: Record<string, any>[];
  totalCount: number;
}

function toLabel(str: string): string {
  return str.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// Fetches all pages (API enforces page_size ≤ 100)
async function fetchAllDocuments(knowledgeId: string): Promise<FetchedData> {
  const PAGE_SIZE = 100;
  const first = await getDataViewTabular(knowledgeId, {
    page_size: PAGE_SIZE,
    page: 1,
    sort: [],
  });

  if (first.total_pages <= 1) {
    return {
      columns: first.columns,
      rows: first.rows,
      totalCount: first.total_count,
    };
  }

  const pageNums = Array.from(
    { length: first.total_pages - 1 },
    (_, i) => i + 2,
  );
  const rest = await Promise.all(
    pageNums.map((p) =>
      getDataViewTabular(knowledgeId, {
        page_size: PAGE_SIZE,
        page: p,
        sort: [],
      }),
    ),
  );

  const allRows = [...first.rows, ...rest.flatMap((p) => p.rows)];
  const seen = new Set<string>();
  const uniqueRows = allRows.filter((row) => {
    if (seen.has(row.object_id)) return false;
    seen.add(row.object_id);
    return true;
  });

  return {
    columns: first.columns,
    rows: uniqueRows,
    totalCount: first.total_count,
  };
}

function deriveExplorableFields(columns: ColumnSchema[]): FieldDef[] {
  const result: FieldDef[] = [];
  const seen = new Set<string>();

  // Group columns by original_field
  const groups = new Map<string, ColumnSchema[]>();
  columns.forEach((col) => {
    if (!col.original_field) return;
    if (EXCLUDED_ORIGINAL_FIELDS.has(col.original_field)) return;
    const existing = groups.get(col.original_field) ?? [];
    groups.set(col.original_field, [...existing, col]);
  });

  groups.forEach((cols, originalField) => {
    const parentType = cols[0].parent_type ?? "";

    if (parentType === "scalar") {
      // Single column, row value is a primitive
      const colName = cols[0].name;
      if (seen.has(colName)) return;
      seen.add(colName);
      result.push({
        key: colName,
        label: toLabel(originalField),
        columnNames: [colName],
        extractFn: (row) => {
          const val = row[colName];
          return val != null && val !== "" ? [String(val)] : [];
        },
      });
    } else if (parentType === "list_str") {
      // Single column, row value is already an array of strings
      const colName = cols[0].name;
      if (seen.has(colName)) return;
      seen.add(colName);
      result.push({
        key: colName,
        label: toLabel(originalField),
        columnNames: [colName],
        extractFn: (row) => {
          const val = row[colName];
          if (Array.isArray(val))
            return val.filter((v) => v != null && v !== "").map(String);
          if (val != null && val !== "") return [String(val)];
          return [];
        },
      });
    } else if (parentType === "dict") {
      // Each subcolumn (address_city, address_country, …) becomes its own field tab
      cols.forEach((col) => {
        if (!col.subcolumn || seen.has(col.name)) return;
        seen.add(col.name);
        result.push({
          key: col.name,
          label: `${toLabel(originalField)} › ${toLabel(col.subcolumn)}`,
          columnNames: [col.name],
          extractFn: (row) => {
            const val = row[col.name];
            return val != null && val !== "" ? [String(val)] : [];
          },
        });
      });
    } else if (parentType === "list_dict") {
      // Group by subcolumn; each unique subcolumn becomes one field tab.
      // Collects values from all indexed positions (_0_, _1_, _remaining_).
      const subGroups = new Map<string, string[]>();
      cols.forEach((col) => {
        if (!col.subcolumn) return;
        const existing = subGroups.get(col.subcolumn) ?? [];
        subGroups.set(col.subcolumn, [...existing, col.name]);
      });

      subGroups.forEach((colNames, subcolumn) => {
        const fieldKey = `${originalField}_${subcolumn}`;
        if (seen.has(fieldKey)) return;
        seen.add(fieldKey);
        result.push({
          key: fieldKey,
          label: `${toLabel(originalField)} › ${toLabel(subcolumn)}`,
          columnNames: colNames,
          extractFn: (row) =>
            colNames
              .map((n) => row[n])
              .filter((v) => v != null && v !== "")
              .flatMap((v) =>
                Array.isArray(v) ? v.filter(Boolean).map(String) : [String(v)],
              ),
        });
      });
    }
  });

  return result;
}

function computeValueCounts(
  rows: Record<string, any>[],
  field: FieldDef,
): ValueCount[] {
  const counts = new Map<string, number>();
  rows.forEach((row) => {
    field.extractFn(row).forEach((val) => {
      counts.set(val, (counts.get(val) ?? 0) + 1);
    });
  });
  return Array.from(counts.entries())
    .map(([value, count]) => ({ value, count }))
    .sort((a, b) => b.count - a.count);
}

export const ExploreView = () => {
  const { knowledgeId } = useAuth();
  const [activeFieldKey, setActiveFieldKey] = useState<string>("");
  const [selectedValue, setSelectedValue] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["exploreView", knowledgeId],
    queryFn: () => fetchAllDocuments(knowledgeId!),
    enabled: Boolean(knowledgeId),
    staleTime: 60_000,
  });

  const fields = useMemo(
    () => deriveExplorableFields(data?.columns ?? []),
    [data?.columns],
  );

  const resolvedFieldKey =
    activeFieldKey && fields.some((f) => f.key === activeFieldKey)
      ? activeFieldKey
      : (fields[0]?.key ?? "");

  const activeField = fields.find((f) => f.key === resolvedFieldKey);

  const valueCounts = useMemo(
    () =>
      activeField ? computeValueCounts(data?.rows ?? [], activeField) : [],
    [data?.rows, activeField],
  );

  const maxCount = valueCounts[0]?.count ?? 1;

  const filteredValueCounts = useMemo(
    () =>
      search
        ? valueCounts.filter((v) =>
            v.value.toLowerCase().includes(search.toLowerCase()),
          )
        : valueCounts,
    [valueCounts, search],
  );

  const matchedRows = useMemo(() => {
    if (!selectedValue || !activeField) return [];
    return (data?.rows ?? []).filter((row) =>
      activeField.extractFn(row).includes(selectedValue),
    );
  }, [data?.rows, activeField, selectedValue]);

  // Context columns shown in the doc table — exclude any that overlap with the active field
  const activeColNames = useMemo(
    () => new Set(activeField?.columnNames ?? []),
    [activeField],
  );
  const availableColNames = useMemo(
    () => new Set(data?.columns.map((c) => c.name) ?? []),
    [data?.columns],
  );
  const contextColumns = useMemo(
    () =>
      CONTEXT_COLUMNS.filter(
        ({ key }) =>
          availableColNames.has(key) &&
          !activeColNames.has(key) &&
          key !== activeField?.key,
      ),
    [availableColNames, activeColNames, activeField?.key],
  );

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <p className="text-sm text-slate-500">Loading...</p>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-3">
        <p className="text-sm text-red-600">Failed to load data.</p>
        <button
          type="button"
          onClick={() => refetch()}
          className="text-xs text-indigo-600 underline cursor-pointer"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!data || fields.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <p className="text-sm text-slate-500">No document fields found.</p>
      </div>
    );
  }

  const tableHeaders = [
    "#",
    "Summary",
    activeField?.label ?? "Field",
    ...contextColumns.map(({ label }) => label),
  ];

  return (
    <div className="space-y-4">
      {/* Field tabs */}
      <div className="flex flex-wrap gap-2">
        {fields.map((field) => {
          const isActive = field.key === resolvedFieldKey;
          return (
            <button
              key={field.key}
              type="button"
              onClick={() => {
                setActiveFieldKey(field.key);
                setSelectedValue(null);
                setSearch("");
              }}
              className={cn(
                "rounded-md border px-3 py-1.5 text-xs font-medium transition-all cursor-pointer",
                isActive
                  ? "border-indigo-300 bg-indigo-50 text-indigo-700"
                  : "border-slate-200 bg-white text-slate-500 hover:bg-slate-50 hover:text-slate-800",
              )}
            >
              {field.label}
            </button>
          );
        })}
      </div>

      {/* Two-panel layout */}
      <div className="flex gap-5 items-start">
        {/* Left: Value list */}
        <div className="w-[360px] shrink-0 rounded-lg border border-slate-200 bg-white shadow-sm overflow-hidden">
          <div className="border-b border-slate-200 p-3">
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 size-3.5 text-slate-400" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={`Search ${activeField?.label.toLowerCase() ?? ""}…`}
                className="w-full rounded-md border border-slate-200 bg-slate-50 py-1.5 pl-8 pr-3 text-xs outline-none placeholder:text-slate-400 focus:border-indigo-400 focus:bg-white"
              />
            </div>
            <p className="mt-2 text-xs text-slate-400">
              {filteredValueCounts.length} unique value
              {filteredValueCounts.length !== 1 ? "s" : ""}
              {data.totalCount > 0 && (
                <span className="ml-2">· {data.totalCount} documents</span>
              )}
            </p>
          </div>

          <div className="max-h-[480px] overflow-y-auto">
            {filteredValueCounts.map((v) => {
              const isSelected = selectedValue === v.value;
              const barPct = Math.max((v.count / maxCount) * 100, 6);
              return (
                <button
                  key={v.value}
                  type="button"
                  onClick={() => setSelectedValue(isSelected ? null : v.value)}
                  className={cn(
                    "w-full border-b border-slate-100 px-3 py-2.5 text-left transition-colors cursor-pointer",
                    isSelected ? "bg-indigo-50" : "hover:bg-slate-50",
                  )}
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <span
                      className={cn(
                        "text-xs",
                        isSelected
                          ? "font-semibold text-indigo-700"
                          : "text-slate-800",
                      )}
                    >
                      {v.value}
                    </span>
                    <span
                      className={cn(
                        "rounded-full px-1.5 py-0.5 text-[10px] font-semibold",
                        isSelected
                          ? "bg-indigo-200 text-indigo-700"
                          : "bg-slate-100 text-slate-600",
                      )}
                    >
                      {v.count}
                    </span>
                  </div>
                  <div className="h-1 rounded-full bg-slate-100 overflow-hidden">
                    <div
                      className={cn(
                        "h-full rounded-full",
                        isSelected ? "bg-indigo-600" : "bg-indigo-300",
                      )}
                      style={{ width: `${barPct}%` }}
                    />
                  </div>
                </button>
              );
            })}
            {filteredValueCounts.length === 0 && (
              <p className="px-3 py-8 text-center text-xs text-slate-400">
                {search
                  ? `No values match "${search}"`
                  : "No values found for this field"}
              </p>
            )}
          </div>
        </div>

        {/* Right: Document table */}
        <div className="flex-1 min-w-0 rounded-lg border border-slate-200 bg-white shadow-sm overflow-hidden">
          {!selectedValue ? (
            <div className="flex flex-col items-center justify-center py-20 gap-2">
              <svg
                width="40"
                height="40"
                viewBox="0 0 40 40"
                className="opacity-20"
              >
                <path
                  d="M8 12H32M12 20H28M16 28H24"
                  stroke="#94a3b8"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                />
              </svg>
              <p className="text-sm text-slate-400">
                Click a value on the left to see matching documents
              </p>
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-4 py-2.5">
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <span>{activeField?.label} =</span>
                  <span className="rounded bg-indigo-50 px-2 py-0.5 text-xs font-semibold text-indigo-700 border border-indigo-200">
                    {selectedValue}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-slate-500">
                    <strong className="text-slate-800">
                      {matchedRows.length}
                    </strong>{" "}
                    document{matchedRows.length !== 1 ? "s" : ""}
                  </span>
                  <button
                    type="button"
                    onClick={() => setSelectedValue(null)}
                    className="flex size-5 items-center justify-center rounded border border-slate-200 bg-white text-slate-400 hover:bg-red-50 hover:text-red-500 cursor-pointer"
                    aria-label="Clear selection"
                  >
                    ✕
                  </button>
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr className="bg-slate-50">
                      {tableHeaders.map((h) => (
                        <th
                          key={h}
                          className="border-b border-slate-200 px-4 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-slate-500 whitespace-nowrap"
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {matchedRows.map((row, i) => (
                      <tr
                        key={row.object_id ?? i}
                        className="border-b border-slate-100 hover:bg-slate-50"
                      >
                        <td className="px-4 py-2.5 text-slate-400">{i + 1}</td>
                        <td className="max-w-[220px] overflow-hidden text-ellipsis whitespace-nowrap px-4 py-2.5 text-slate-700">
                          {row.summary ?? "—"}
                        </td>
                        <td className="px-4 py-2.5">
                          <div className="flex flex-wrap gap-1">
                            {activeField?.extractFn(row).map((val) => (
                              <span
                                key={val}
                                className={cn(
                                  "rounded px-1.5 py-0.5 text-[11px] font-medium",
                                  val === selectedValue
                                    ? "border border-indigo-300 bg-indigo-50 text-indigo-700"
                                    : "bg-blue-50 text-blue-700",
                                )}
                              >
                                {val}
                              </span>
                            ))}
                          </div>
                        </td>
                        {contextColumns.map(({ key }) => (
                          <td
                            key={key}
                            className="px-4 py-2.5 text-slate-500 whitespace-nowrap max-w-[180px] overflow-hidden text-ellipsis"
                          >
                            {row[key] ?? "—"}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
