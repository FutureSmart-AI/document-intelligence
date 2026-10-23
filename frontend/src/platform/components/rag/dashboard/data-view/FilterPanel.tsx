"use client";

import React, { useCallback, useMemo, useState } from "react";

import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Circle,
  CircleOff,
  Equal,
  EqualNot,
  Filter,
  List,
  Plus,
  Search,
  SearchX,
  X,
} from "lucide-react";

import {
  Button,
  Input,
  RichSelect,
  Select,
  type RichSelectOption,
} from "@/platform/components/ui";
import type {
  ColumnSchema,
  DataType,
  FilterValue,
} from "@/platform/types/data-view";
import {
  normalizeInValueForApi,
  parseInListInput,
  stringifyInListForInput,
} from "@/platform/utils/in-list-filter";

interface FilterRule {
  id: string;
  field: string;
  operator:
    | "eq"
    | "ne"
    | "contains"
    | "not_contains"
    | "is_empty"
    | "is_not_empty"
    | "gte"
    | "lte"
    | "gt"
    | "lt"
    | "in";
  value: string | number | string[];
}

interface FilterPanelProps {
  columns: ColumnSchema[];
  filters: Record<string, FilterValue>;
  onFiltersChange: (filters: Record<string, FilterValue>) => void;
  onClear: () => void;
}

const OPERATOR_CONFIG: Record<
  | "eq"
  | "ne"
  | "contains"
  | "not_contains"
  | "is_empty"
  | "is_not_empty"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "in",
  { icon: React.ReactNode; label: string }
> = {
  eq: { icon: <Equal className="size-3.5" />, label: "Equals" },
  ne: { icon: <EqualNot className="size-3.5" />, label: "Not equals" },
  contains: { icon: <Search className="size-3.5" />, label: "Contains" },
  not_contains: {
    icon: <SearchX className="size-3.5" />,
    label: "Not contains",
  },
  is_empty: { icon: <CircleOff className="size-3.5" />, label: "Is empty" },
  is_not_empty: {
    icon: <Circle className="size-3.5" />,
    label: "Is not empty",
  },
  gt: { icon: <ChevronRight className="size-3.5" />, label: "Greater than" },
  gte: {
    icon: <ChevronsRight className="size-3.5" />,
    label: "Greater or equal",
  },
  lt: { icon: <ChevronLeft className="size-3.5" />, label: "Less than" },
  lte: { icon: <ChevronsLeft className="size-3.5" />, label: "Less or equal" },
  in: { icon: <List className="size-3.5" />, label: "In" },
};

const OPERATOR_OPTIONS: RichSelectOption<FilterRule["operator"]>[] =
  Object.entries(OPERATOR_CONFIG).map(([value, { label }]) => ({
    label,
    value: value as FilterRule["operator"],
  }));

export const FilterPanel = ({
  columns,
  filters,
  onFiltersChange,
  onClear,
}: FilterPanelProps) => {
  const [filterRules, setFilterRules] = useState<FilterRule[]>([]);
  const [isExpanded, setIsExpanded] = useState(false);

  // Get unique original fields for filter selection
  const availableFields = useMemo(() => {
    const fields = new Set<string>();
    columns.forEach((col) => {
      fields.add(col.field_path || col.original_field || col.name);
    });
    return Array.from(fields).sort();
  }, [columns]);

  const filterFieldTypeMap = useMemo(() => {
    const map = new Map<string, DataType | undefined>();
    columns.forEach((col) => {
      const fieldPath = col.field_path || col.original_field || col.name;
      if (
        !map.has(fieldPath) ||
        (map.get(fieldPath) === undefined && col.data_type)
      ) {
        map.set(fieldPath, col.data_type);
      }
    });
    return map;
  }, [columns]);

  // Convert filters to rules for editing
  useMemo(() => {
    const rules: FilterRule[] = [];
    Object.entries(filters).forEach(([field, value]) => {
      if (value === null || value === undefined) return;

      if (value.op === "in") {
        const raw = value.value;
        const items = Array.isArray(raw) ? raw.map((v) => String(v)) : [];
        rules.push({
          id: `${field}-${value.op}`,
          field,
          operator: "in",
          value: stringifyInListForInput(items),
        });
      } else {
        rules.push({
          id: `${field}-${value.op}`,
          field,
          operator: value.op,
          value:
            value.op === "is_empty" || value.op === "is_not_empty"
              ? ""
              : typeof value.value === "number"
                ? value.value
                : (String(value.value) ?? ""),
        });
      }
    });
    setFilterRules(rules);
  }, [filters]);

  const addFilterRule = useCallback(() => {
    const newRule: FilterRule = {
      id: `filter-${Date.now()}`,
      field: availableFields[0] || "",
      operator: "eq",
      value: "",
    };
    setFilterRules((prev) => [...prev, newRule]);
  }, [availableFields]);

  const removeFilterRule = useCallback((id: string) => {
    setFilterRules((prev) => prev.filter((rule) => rule.id !== id));
  }, []);

  const updateFilterRule = useCallback(
    (id: string, updates: Partial<FilterRule>) => {
      console.log("updates", updates, id);
      setFilterRules((prev) =>
        prev.map((rule) => (rule.id === id ? { ...rule, ...updates } : rule)),
      );
    },
    [],
  );

  const applyFilters = useCallback(() => {
    const newFilters: Record<string, FilterValue> = {};

    filterRules.forEach((rule) => {
      if (!rule.field) {
        return;
      }

      if (rule.operator === "is_empty" || rule.operator === "is_not_empty") {
        newFilters[rule.field] = { op: rule.operator };
        return;
      }

      if (rule.operator === "in") {
        const raw =
          typeof rule.value === "string"
            ? rule.value
            : stringifyInListForInput(
                Array.isArray(rule.value) ? rule.value.map(String) : [],
              );
        const stringItems = parseInListInput(raw);
        if (stringItems.length === 0) return;
        newFilters[rule.field] = {
          op: "in",
          value: normalizeInValueForApi(
            stringItems,
            filterFieldTypeMap.get(rule.field),
          ),
        };
        return;
      }

      if (
        rule.value === "" ||
        (Array.isArray(rule.value) && rule.value.length === 0)
      ) {
        return;
      }

      newFilters[rule.field] = { op: rule.operator, value: rule.value };
    });

    onFiltersChange(newFilters);
  }, [filterRules, filterFieldTypeMap, onFiltersChange]);

  const handleClear = useCallback(() => {
    setFilterRules([]);
    onClear();
  }, [onClear]);

  const hasActiveFilters =
    filterRules.length > 0 || Object.keys(filters).length > 0;

  return (
    <div className="bg-white rounded-lg border border-slate-200 shadow-sm">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between p-4 hover:bg-slate-50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Filter className="size-4 text-slate-600" />
          <span className="font-medium text-slate-900">Filters</span>
          {hasActiveFilters && (
            <span className="px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 text-xs font-medium">
              {filterRules.length || Object.keys(filters).length}
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
          {filterRules.length === 0 ? (
            <div className="text-center py-4 text-sm text-slate-500">
              No filters applied. Click "Add Filter" to get started.
            </div>
          ) : (
            <div className="space-y-3">
              {filterRules.map((rule) => (
                <div
                  key={rule.id}
                  className="flex items-start gap-2 p-3 bg-slate-50 rounded-lg"
                >
                  <div className="flex-1 grid grid-cols-12 gap-2">
                    <div className="col-span-4">
                      <Select
                        options={availableFields.map((field) => ({
                          label: field.replace(/_/g, " ").replace(/\./g, " → "),
                          value: field,
                        }))}
                        value={rule.field}
                        onChange={(v) =>
                          updateFilterRule(rule.id, { field: v })
                        }
                        size="sm"
                        placeholder="Select field"
                      />
                    </div>
                    <div className="col-span-3">
                      <RichSelect
                        options={OPERATOR_OPTIONS}
                        value={rule.operator}
                        onChange={(v) =>
                          updateFilterRule(rule.id, {
                            operator: v,
                            value: "",
                          })
                        }
                        size="sm"
                        renderOption={(option, isSelected) => {
                          const config = OPERATOR_CONFIG[option.value];
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
                                {config.icon}
                              </span>
                              <span className="text-sm">{option.label}</span>
                              {isSelected && (
                                <Check className="ml-auto size-3 text-indigo-600" />
                              )}
                            </div>
                          );
                        }}
                        renderValue={(option) => {
                          const config = OPERATOR_CONFIG[option.value];
                          return (
                            <div className="flex items-center gap-1.5">
                              <span className="text-slate-400">
                                {config.icon}
                              </span>
                              <span>{option.label}</span>
                            </div>
                          );
                        }}
                      />
                    </div>
                    <div className="col-span-4">
                      {rule.operator === "is_empty" ||
                      rule.operator === "is_not_empty" ? (
                        <div className="h-8 rounded-md border border-slate-200 bg-slate-100 px-2.5 text-xs text-slate-500 flex items-center">
                          No value required
                        </div>
                      ) : rule.operator === "in" ? (
                        <Input
                          as="textarea"
                          rows={3}
                          placeholder={
                            "One value per line, or comma-separated\nExample:\nDev\nAnalyst"
                          }
                          value={
                            typeof rule.value === "string"
                              ? rule.value
                              : stringifyInListForInput(
                                  Array.isArray(rule.value)
                                    ? rule.value.map(String)
                                    : [],
                                )
                          }
                          onChange={(v) =>
                            updateFilterRule(rule.id, { value: v })
                          }
                          size="sm"
                          className="min-h-18 resize-y font-mono text-xs"
                        />
                      ) : (
                        <Input
                          type={
                            typeof rule.value === "number" ? "number" : "text"
                          }
                          placeholder="Enter value"
                          value={String(rule.value || "")}
                          onChange={(v) => {
                            const numValue = Number(v);
                            updateFilterRule(rule.id, {
                              value: isNaN(numValue) ? v : numValue,
                            });
                          }}
                          size="sm"
                        />
                      )}
                    </div>
                    <div className="col-span-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => removeFilterRule(rule.id)}
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
              onClick={addFilterRule}
              startIcon={<Plus className="size-4" />}
            >
              Add Filter
            </Button>
            <div className="flex items-center gap-2">
              {hasActiveFilters && (
                <Button variant="outline" size="sm" onClick={handleClear}>
                  Clear All
                </Button>
              )}
              <Button variant="primary" size="sm" onClick={applyFilters}>
                Apply Filters
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
