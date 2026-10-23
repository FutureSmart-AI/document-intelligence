"use client";

import { useCallback, useMemo, useState } from "react";

import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronDown,
  Plus,
  X,
} from "lucide-react";

import {
  Button,
  RichSelect,
  Select,
  type RichSelectOption,
} from "@/platform/components/ui";
import type { ColumnSchema, SortCriteria } from "@/platform/types/data-view";

interface SortPanelProps {
  columns: ColumnSchema[];
  sort: SortCriteria[];
  onSortChange: (sort: SortCriteria[]) => void;
  onClear: () => void;
}

const DIRECTION_OPTIONS: (RichSelectOption<"asc" | "desc"> & {
  icon: React.ReactNode;
  hint: string;
})[] = [
  {
    label: "Ascending",
    value: "asc",
    icon: <ArrowUp className="size-3.5" />,
    hint: "A→Z",
  },
  {
    label: "Descending",
    value: "desc",
    icon: <ArrowDown className="size-3.5" />,
    hint: "Z→A",
  },
];

export const SortPanel = ({
  columns,
  sort,
  onSortChange,
  onClear,
}: SortPanelProps) => {
  const [sortRules, setSortRules] = useState<SortCriteria[]>(sort);
  const [isExpanded, setIsExpanded] = useState(false);

  // Get available fields for sorting
  const availableFields = useMemo(() => {
    const fields = new Set<string>();
    columns.forEach((col) => {
      fields.add(col.original_field || col.name);
    });
    return Array.from(fields).sort();
  }, [columns]);

  // Sync sort rules with prop
  useMemo(() => {
    if (
      sort.length !== sortRules.length ||
      sort.some(
        (s, i) =>
          s.field !== sortRules[i]?.field || s.order !== sortRules[i]?.order,
      )
    ) {
      setSortRules(sort);
    }
  }, [sort]);

  const addSortRule = useCallback(() => {
    const newRule: SortCriteria = {
      field: availableFields[0] || "",
      order: "asc",
    };
    setSortRules((prev) => [...prev, newRule]);
  }, [availableFields]);

  const removeSortRule = useCallback((index: number) => {
    setSortRules((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const updateSortRule = useCallback(
    (index: number, updates: Partial<SortCriteria>) => {
      setSortRules((prev) =>
        prev.map((rule, i) => (i === index ? { ...rule, ...updates } : rule)),
      );
    },
    [],
  );

  const applySort = useCallback(() => {
    onSortChange(sortRules.filter((rule) => rule.field));
  }, [sortRules, onSortChange]);

  const handleClear = useCallback(() => {
    setSortRules([]);
    onClear();
  }, [onClear]);

  const hasActiveSort = sortRules.length > 0 && sortRules.some((r) => r.field);

  return (
    <div className="bg-white rounded-lg border border-slate-200 shadow-sm">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between p-4 hover:bg-slate-50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <ArrowUpDown className="size-4 text-slate-600" />
          <span className="font-medium text-slate-900">Sort</span>
          {hasActiveSort && (
            <span className="px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 text-xs font-medium">
              {sortRules.filter((r) => r.field).length}
            </span>
          )}
        </div>
        <ChevronDown
          className={`size-4 text-slate-600 transition-transform ${
            isExpanded ? "rotate-180" : ""
          }`}
        />
      </button>

      {isExpanded && (
        <div className="p-4 border-t border-slate-200 space-y-4">
          {sortRules.length === 0 ? (
            <div className="text-center py-4 text-sm text-slate-500">
              No sort rules. Click "Add Sort" to get started.
            </div>
          ) : (
            <div className="space-y-3">
              {sortRules.map((rule, index) => (
                <div
                  key={index}
                  className="flex items-start gap-2 p-3 bg-slate-50 rounded-lg"
                >
                  <div className="flex-1 grid grid-cols-12 gap-2">
                    <div className="col-span-1 flex items-center justify-center">
                      <span className="text-xs font-medium text-slate-500">
                        {index + 1}
                      </span>
                    </div>
                    <div className="col-span-7">
                      <Select
                        options={availableFields.map((field) => ({
                          label: field.replace(/_/g, " ").replace(/\./g, " → "),
                          value: field,
                        }))}
                        value={rule.field}
                        onChange={(v) => updateSortRule(index, { field: v })}
                        size="sm"
                        placeholder="Select field"
                      />
                    </div>
                    <div className="col-span-3">
                      <RichSelect
                        options={DIRECTION_OPTIONS}
                        value={rule.order}
                        onChange={(v) => updateSortRule(index, { order: v })}
                        size="sm"
                        renderOption={(option, isSelected) => {
                          const dir = DIRECTION_OPTIONS.find(
                            (d) => d.value === option.value,
                          )!;
                          return (
                            <div
                              className={`flex items-center gap-2 px-3 py-2 ${
                                isSelected
                                  ? "text-indigo-700"
                                  : "text-slate-700"
                              }`}
                            >
                              <span
                                className={
                                  isSelected
                                    ? "text-indigo-600"
                                    : "text-slate-400"
                                }
                              >
                                {dir.icon}
                              </span>
                              <span className="text-sm">{option.label}</span>
                              <span className="ml-auto text-xs text-slate-400">
                                {dir.hint}
                              </span>
                            </div>
                          );
                        }}
                        renderValue={(option) => {
                          const dir = DIRECTION_OPTIONS.find(
                            (d) => d.value === option.value,
                          )!;
                          return (
                            <div className="flex items-center gap-1.5">
                              <span className="text-slate-400">{dir.icon}</span>
                              <span>{option.label}</span>
                            </div>
                          );
                        }}
                      />
                    </div>
                    <div className="col-span-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => removeSortRule(index)}
                        startIcon={<X className="size-4" />}
                      />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="flex items-center justify-between pt-2 border-t border-slate-200">
            <Button
              variant="outline"
              size="sm"
              onClick={addSortRule}
              startIcon={<Plus className="size-4" />}
            >
              Add Sort
            </Button>
            <div className="flex items-center gap-2">
              {hasActiveSort && (
                <Button variant="outline" size="sm" onClick={handleClear}>
                  Clear All
                </Button>
              )}
              <Button variant="primary" size="sm" onClick={applySort}>
                Apply Sort
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
