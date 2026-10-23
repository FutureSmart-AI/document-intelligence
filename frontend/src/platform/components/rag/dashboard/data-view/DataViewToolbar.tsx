"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  ArrowDownAZ,
  ArrowUpAZ,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Circle,
  CircleOff,
  Columns,
  Download,
  Equal,
  EqualNot,
  Filter,
  Layers,
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
  type RichSelectOption,
} from "@/platform/components/ui";
import type {
  ColumnSchema,
  DataType,
  FilterValue,
  SortCriteria,
} from "@/platform/types/data-view";
import {
  normalizeInValueForApi,
  parseInListInput,
  stringifyInListForInput,
} from "@/platform/utils/in-list-filter";

import {
  FilterRule,
  NUMERIC_TYPES,
  OPERATOR_OPTIONS,
  TEXT_TYPES,
  VALUE_REQUIRED_OPERATORS,
} from "./constants";

interface DataViewToolbarProps {
  columns: ColumnSchema[];
  selectedColumns: string[];
  filters: Record<string, FilterValue>;
  sort: SortCriteria[];
  onColumnsChange: (columns: string[]) => void;
  onFiltersChange: (filters: Record<string, FilterValue>) => void;
  onSortChange: (sort: SortCriteria[]) => void;
  onClearColumns: () => void;
  onClearFilters: () => void;
  onClearSort: () => void;
  /** Exports the whole filtered result set (all pages) as CSV. */
  onExportCsv?: () => void;
  isExporting?: boolean;
  /** Row counter shown on the button while pages are being fetched. */
  exportProgress?: { fetched: number; total: number } | null;
  exportDisabled?: boolean;
}

type ActivePanel = "sort" | "filters" | null;

const OPERATOR_ICON_MAP: Record<FilterRule["operator"], ReactNode> = {
  eq: <Equal className="size-3.5" />,
  ne: <EqualNot className="size-3.5" />,
  contains: <Search className="size-3.5" />,
  not_contains: <SearchX className="size-3.5" />,
  is_empty: <CircleOff className="size-3.5" />,
  is_not_empty: <Circle className="size-3.5" />,
  gt: <ChevronRight className="size-3.5" />,
  gte: <ChevronsRight className="size-3.5" />,
  lt: <ChevronLeft className="size-3.5" />,
  lte: <ChevronsLeft className="size-3.5" />,
  in: <List className="size-3.5" />,
};

const getOperatorOptionsForField = (
  dataType?: DataType,
): { label: string; value: FilterRule["operator"] }[] => {
  if (!dataType) {
    return OPERATOR_OPTIONS;
  }

  if (NUMERIC_TYPES.has(dataType)) {
    return OPERATOR_OPTIONS.filter((option) =>
      [
        "eq",
        "ne",
        "gte",
        "lte",
        "gt",
        "lt",
        "in",
        "is_empty",
        "is_not_empty",
      ].includes(option.value),
    );
  }

  if (dataType === "bool") {
    return OPERATOR_OPTIONS.filter((option) =>
      ["eq", "ne", "is_empty", "is_not_empty"].includes(option.value),
    );
  }

  if (TEXT_TYPES.has(dataType)) {
    return OPERATOR_OPTIONS.filter((option) =>
      [
        "eq",
        "ne",
        "contains",
        "not_contains",
        "in",
        "is_empty",
        "is_not_empty",
      ].includes(option.value),
    );
  }

  // dict / List[dict] fallback: keep broad options for mixed nested leaf values
  return OPERATOR_OPTIONS.filter((option) =>
    [
      "eq",
      "ne",
      "contains",
      "not_contains",
      "in",
      "is_empty",
      "is_not_empty",
    ].includes(option.value),
  );
};

export const DataViewToolbar = ({
  columns,
  selectedColumns,
  filters,
  sort,
  onColumnsChange,
  onFiltersChange,
  onSortChange,
  onClearColumns,
  onClearFilters,
  onClearSort,
  onExportCsv,
  isExporting = false,
  exportProgress = null,
  exportDisabled = false,
}: DataViewToolbarProps) => {
  const [activePanel, setActivePanel] = useState<ActivePanel>(null);
  const [filterRules, setFilterRules] = useState<FilterRule[]>([]);
  const [sortRules, setSortRules] = useState<SortCriteria[]>(sort);
  const [isColumnsOpen, setIsColumnsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const columnsContainerRef = useRef<HTMLDivElement>(null);

  // Draft state for columns
  const [draftColumns, setDraftColumns] = useState<string[]>([]);

  // Sync draft columns when opening
  useEffect(() => {
    setDraftColumns(selectedColumns);
  }, [selectedColumns]);

  // Get unique original fields for column selection
  const availableFields = useMemo(() => {
    const fields = new Map<string, { name: string; dataTypes: Set<string> }>();

    columns.forEach((col) => {
      const fieldName = col.original_field || col.name;
      if (!fields.has(fieldName)) {
        fields.set(fieldName, { name: fieldName, dataTypes: new Set() });
      }
      fields
        .get(fieldName)!
        .dataTypes.add(col.data_type || col.parent_type || "");
    });

    return Array.from(fields.entries())
      .map(([name, info]) => ({
        name,
        displayName: name.replace(/_/g, " "),
        dataTypes: Array.from(info.dataTypes),
      }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [columns]);

  // Filtered fields for column search
  const filteredFields = useMemo(() => {
    if (!searchTerm.trim()) return availableFields;
    const search = searchTerm.toLowerCase();
    return availableFields.filter(
      (field) =>
        field.name.toLowerCase().includes(search) ||
        field.displayName.toLowerCase().includes(search),
    );
  }, [availableFields, searchTerm]);

  const fieldGroups = useMemo(() => {
    const groups = new Map<
      string,
      {
        originalField: string;
        requiresSubfield: boolean;
        subcolumns: {
          label: string;
          value: string;
          fieldPath: string;
          dataType?: DataType;
        }[];
        defaultFieldPath: string;
        defaultDataType?: DataType;
      }
    >();

    columns.forEach((col) => {
      const of = (col.original_field || col.name || "").trim();
      if (!of) return;

      if (!groups.has(of)) {
        groups.set(of, {
          originalField: of,
          requiresSubfield: false,
          subcolumns: [],
          defaultFieldPath: col.field_path || col.original_field || col.name,
          defaultDataType: col.data_type,
        });
      }
      const group = groups.get(of)!;
      if (col.parent_type === "dict" || col.parent_type === "list_dict") {
        group.requiresSubfield = true;
        if (col.subcolumn) {
          if (!group.subcolumns.some((s) => s.value === col.subcolumn)) {
            group.subcolumns.push({
              label: col.subcolumn,
              value: col.subcolumn,
              fieldPath: col.field_path || col.original_field || col.name,
              dataType: col.data_type,
            });
          }
        }
      }
    });

    // Sort subcolumns
    for (const group of groups.values()) {
      group.subcolumns.sort((a, b) => a.label.localeCompare(b.label));
    }

    return groups;
  }, [columns]);

  const originalFieldOptions = useMemo(() => {
    return Array.from(fieldGroups.keys()).filter(Boolean).sort();
  }, [fieldGroups]);

  const sortFieldGroups = useMemo(() => {
    const groups = new Map<
      string,
      {
        originalField: string;
        requiresSubfield: boolean;
        subcolumns: { label: string; value: string; sortField: string }[];
        defaultSortField?: string;
      }
    >();

    columns.forEach((col) => {
      if (col.sort_field === null || col.sort_field === undefined) {
        return;
      }

      const of = (col.original_field || col.name || "").trim();
      if (!of) return;

      if (!groups.has(of)) {
        groups.set(of, {
          originalField: of,
          requiresSubfield: false,
          subcolumns: [],
          defaultSortField: col.sort_field,
        });
      }
      const group = groups.get(of)!;
      if (col.parent_type === "dict" || col.parent_type === "list_dict") {
        group.requiresSubfield = true;
        if (col.subcolumn) {
          if (!group.subcolumns.some((s) => s.value === col.subcolumn)) {
            group.subcolumns.push({
              label: col.subcolumn,
              value: col.subcolumn,
              sortField: col.sort_field,
            });
          }
        }
      }
    });

    for (const group of groups.values()) {
      group.subcolumns.sort((a, b) => a.label.localeCompare(b.label));
    }

    return groups;
  }, [columns]);

  const sortOriginalFieldOptions = useMemo(() => {
    return Array.from(sortFieldGroups.keys()).filter(Boolean).sort();
  }, [sortFieldGroups]);

  // Convert filters to rules for editing
  useEffect(() => {
    const rules: FilterRule[] = [];
    Object.entries(filters).forEach(([fieldPath, value]) => {
      if (value === null || value === undefined) return;

      const col = columns.find(
        (c) => (c.field_path || c.original_field || c.name) === fieldPath,
      );
      const originalField = col?.original_field || fieldPath;
      const subcolumn = col?.subcolumn || undefined;

      if (value.op === "in") {
        const raw = value.value;
        const items = Array.isArray(raw) ? raw.map((v) => String(v)) : [];
        rules.push({
          id: `${fieldPath}-${value.op}`,
          field: fieldPath,
          originalField,
          subcolumn,
          operator: "in",
          value: stringifyInListForInput(items),
        });
      } else {
        rules.push({
          id: `${fieldPath}-${value.op}`,
          field: fieldPath,
          originalField,
          subcolumn,
          operator: value.op,
          value:
            value.op === "is_empty" || value.op === "is_not_empty"
              ? ""
              : typeof value.value === "number"
                ? value.value
                : String(value.value ?? ""),
        });
      }
    });
    setFilterRules(rules);
  }, [filters, columns]);

  // Sync sort rules from props only when they change externally
  useEffect(() => {
    const rules: SortCriteria[] = sort.map((s) => {
      const col = columns.find((c) => c.sort_field === s.field);
      return {
        field: s.field,
        originalField: col?.original_field || s.field,
        subcolumn: col?.subcolumn || undefined,
        order: s.order,
      };
    });
    setSortRules(rules);
  }, [sort, columns]);

  const addFilterRule = useCallback(() => {
    const defaultOriginalField = originalFieldOptions[0] || "";
    const group = fieldGroups.get(defaultOriginalField);

    let defaultFieldPath = "";
    let defaultSubcolumn: string | undefined = undefined;
    let defaultDataType: DataType | undefined = undefined;

    if (group) {
      if (group.requiresSubfield && group.subcolumns.length > 0) {
        defaultSubcolumn = group.subcolumns[0].value;
        defaultFieldPath = group.subcolumns[0].fieldPath;
        defaultDataType = group.subcolumns[0].dataType;
      } else {
        defaultFieldPath = group.defaultFieldPath;
        defaultDataType = group.defaultDataType;
      }
    }

    const defaultOperators = getOperatorOptionsForField(defaultDataType);
    const newRule: FilterRule = {
      id: `filter-${Date.now()}`,
      field: defaultFieldPath,
      originalField: defaultOriginalField,
      subcolumn: defaultSubcolumn,
      operator: defaultOperators[0]?.value || "eq",
      value: "",
    };
    setFilterRules((prev) => [...prev, newRule]);
  }, [originalFieldOptions, fieldGroups]);

  const removeFilterRule = useCallback((id: string) => {
    setFilterRules((prev) => prev.filter((rule) => rule.id !== id));
  }, []);

  const updateFilterRule = useCallback(
    (id: string, updates: Partial<FilterRule>) => {
      setFilterRules((prev) =>
        prev.map((rule) => (rule.id === id ? { ...rule, ...updates } : rule)),
      );
    },
    [],
  );

  const applyFilters = useCallback(() => {
    const newFilters: Record<string, FilterValue> = {};

    filterRules.forEach((rule) => {
      if (!rule.field) return;

      const group = fieldGroups.get(rule.originalField || "");
      const dataType = group?.requiresSubfield
        ? group.subcolumns.find((s) => s.value === rule.subcolumn)?.dataType
        : group?.defaultDataType;

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
        const coerced = normalizeInValueForApi(stringItems, dataType);
        newFilters[rule.field] = { op: "in", value: coerced };
        return;
      }

      if (
        VALUE_REQUIRED_OPERATORS.includes(rule.operator) &&
        (rule.value === "" ||
          (Array.isArray(rule.value) && rule.value.length === 0))
      ) {
        return;
      }

      newFilters[rule.field] = { op: rule.operator, value: rule.value };
    });

    onFiltersChange(newFilters);
  }, [filterRules, fieldGroups, onFiltersChange]);

  const handleClearFilters = useCallback(() => {
    setFilterRules([]);
    onClearFilters();
  }, [onClearFilters]);

  const addSortRule = useCallback(() => {
    const defaultOriginalField = sortOriginalFieldOptions[0] || "";
    const group = sortFieldGroups.get(defaultOriginalField);

    let defaultSortField = "";
    let defaultSubcolumn: string | undefined = undefined;

    if (group) {
      if (group.requiresSubfield && group.subcolumns.length > 0) {
        defaultSubcolumn = group.subcolumns[0].value;
        defaultSortField = group.subcolumns[0].sortField;
      } else {
        defaultSortField = group.defaultSortField || "";
      }
    }

    const newRule: SortCriteria = {
      field: defaultSortField,
      originalField: defaultOriginalField,
      subcolumn: defaultSubcolumn,
      order: "asc",
    };
    setSortRules((prev) => [...prev, newRule]);
  }, [sortOriginalFieldOptions, sortFieldGroups]);

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
    setActivePanel(null);
  }, [sortRules, onSortChange]);

  const handleClearSort = useCallback(() => {
    setSortRules([]);
    onClearSort();
    setActivePanel(null);
  }, [onClearSort]);

  // Column management
  const allColumnsSelected = draftColumns.length === 0;

  const isFieldSelected = useCallback(
    (fieldName: string) => {
      // When "unselect all" is active, nothing is selected
      if (draftColumns.length === 1 && draftColumns[0] === "__none__") {
        return false;
      }
      if (allColumnsSelected) {
        return availableFields.some((f) => f.name === fieldName);
      }
      return draftColumns.includes(fieldName);
    },
    [draftColumns, allColumnsSelected, availableFields],
  );

  const toggleField = useCallback(
    (fieldName: string) => {
      // When "unselect all" is active, start fresh with the toggled field
      if (draftColumns.length === 1 && draftColumns[0] === "__none__") {
        setDraftColumns([fieldName]);
        return;
      }
      if (allColumnsSelected) {
        const allFieldNames = availableFields.map((f) => f.name);
        setDraftColumns(allFieldNames.filter((name) => name !== fieldName));
      } else if (isFieldSelected(fieldName)) {
        const newDraft = draftColumns.filter((col) => col !== fieldName);
        setDraftColumns(newDraft);
      } else {
        setDraftColumns([...draftColumns, fieldName]);
      }
    },
    [draftColumns, allColumnsSelected, availableFields, isFieldSelected],
  );

  const selectAllColumns = useCallback(() => {
    setDraftColumns([]);
  }, []);

  const unselectAllColumns = useCallback(() => {
    // Set draft to a special state where nothing is selected
    // We use a sentinel value to distinguish "none selected" from "all selected" (empty array)
    setDraftColumns(["__none__"]);
  }, []);

  const handleApplyColumns = useCallback(() => {
    // If the special "none selected" sentinel is used, apply as empty selection
    const columnsToApply = draftColumns.includes("__none__")
      ? []
      : draftColumns;
    onColumnsChange(columnsToApply);
    setIsColumnsOpen(false);
  }, [draftColumns, onColumnsChange]);

  const togglePanel = useCallback((panel: ActivePanel) => {
    setActivePanel((prev) => (prev === panel ? null : panel));
  }, []);

  // Close dropdowns when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        columnsContainerRef.current &&
        !columnsContainerRef.current.contains(event.target as Node)
      ) {
        setIsColumnsOpen(false);
        setSearchTerm("");
      }
    };

    if (isColumnsOpen) {
      document.addEventListener("mousedown", handleClickOutside);
      return () =>
        document.removeEventListener("mousedown", handleClickOutside);
    }
    return undefined;
  }, [isColumnsOpen]);

  // Display counts
  const displayColumnCount =
    selectedColumns.length === 0
      ? availableFields.length
      : selectedColumns.length;
  const isNoneSelected =
    draftColumns.length === 1 && draftColumns[0] === "__none__";
  const draftColumnCount = isNoneSelected
    ? 0
    : draftColumns.length === 0
      ? availableFields.length
      : draftColumns.length;

  const hasActiveFilters =
    filterRules.length > 0 || Object.keys(filters).length > 0;
  const hasActiveSort = sortRules.length > 0 && sortRules.some((r) => r.field);

  return (
    <div className="bg-white rounded-lg border border-slate-200">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-2 px-3 py-2">
        <div className="flex items-center gap-1.5">
          {/* Columns */}
          <div ref={columnsContainerRef} className="relative">
            <button
              type="button"
              onClick={() => setIsColumnsOpen(!isColumnsOpen)}
              className={`inline-flex cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                isColumnsOpen
                  ? "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200"
                  : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
              }`}
            >
              <Columns className="size-3.5" />
              Columns
              <span className="ml-0.5 px-1.5 py-0.5 rounded-full bg-slate-100 text-slate-600 text-[10px] font-semibold">
                {displayColumnCount}
              </span>
              <ChevronDown
                className={`size-3 text-slate-400 transition-transform ${isColumnsOpen ? "rotate-180" : ""}`}
              />
            </button>

            {isColumnsOpen && (
              <div className="absolute z-50 mt-1.5 w-[380px] bg-white rounded-lg border border-slate-200 shadow-lg max-h-[400px] flex flex-col">
                <div className="p-2.5 border-b border-slate-100">
                  <div className="relative">
                    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-slate-400" />
                    <input
                      type="text"
                      placeholder="Search columns..."
                      value={searchTerm}
                      onChange={(e) => setSearchTerm(e.target.value)}
                      className="w-full pl-8 pr-3 py-1.5 text-xs border border-slate-200 rounded-md focus:outline-none focus:ring-1 focus:ring-indigo-400 focus:border-indigo-400"
                      autoFocus
                    />
                  </div>
                  <div className="flex items-center justify-between mt-2">
                    <div className="flex items-center gap-2">
                      <Button
                        type="button"
                        size="xs"
                        variant="ghost"
                        onClick={selectAllColumns}
                        disabled={allColumnsSelected && !isNoneSelected}
                      >
                        Select all
                      </Button>
                      <span className="text-slate-300">|</span>
                      <Button
                        type="button"
                        size="xs"
                        variant="ghost"
                        onClick={unselectAllColumns}
                        disabled={isNoneSelected}
                      >
                        Unselect all
                      </Button>
                    </div>
                    <span className="text-[11px] text-slate-400">
                      {draftColumnCount}/{availableFields.length}
                    </span>
                  </div>
                </div>

                <div className="flex-1 overflow-y-auto p-1.5">
                  {filteredFields.length === 0 ? (
                    <div className="text-center py-6 text-xs text-slate-400">
                      No columns match &ldquo;{searchTerm}&rdquo;
                    </div>
                  ) : (
                    <div className="grid grid-cols-2 gap-0.5">
                      {filteredFields.map((field) => {
                        const isSelected = isFieldSelected(field.name);
                        return (
                          <button
                            key={field.name}
                            type="button"
                            onClick={() => toggleField(field.name)}
                            className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors ${
                              isSelected ? "bg-indigo-50" : "hover:bg-slate-50"
                            }`}
                          >
                            <div
                              className={`flex items-center justify-center size-3.5 rounded border shrink-0 transition-colors ${
                                isSelected
                                  ? "bg-indigo-600 border-indigo-600"
                                  : "border-slate-300"
                              }`}
                            >
                              {isSelected && (
                                <Check
                                  className="size-2.5 text-white"
                                  strokeWidth={3}
                                />
                              )}
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="text-xs font-medium text-slate-700 truncate">
                                {field.displayName}
                              </div>
                              <div className="text-[10px] text-slate-400 truncate">
                                {field.dataTypes.join(", ")}
                              </div>
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>

                <div className="p-2 border-t border-slate-100 flex justify-end">
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={handleApplyColumns}
                    className="h-7 text-xs"
                  >
                    Apply
                  </Button>
                </div>
              </div>
            )}
          </div>

          {/* Filters */}
          <button
            type="button"
            onClick={() => togglePanel("filters")}
            className={`inline-flex cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
              activePanel === "filters" || hasActiveFilters
                ? "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200"
                : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
            }`}
          >
            <Filter className="size-3.5" />
            Filters
            {hasActiveFilters && (
              <span className="ml-0.5 px-1.5 py-0.5 rounded-full bg-indigo-600 text-white text-[10px] font-semibold">
                {filterRules.length || Object.keys(filters).length}
              </span>
            )}
            <ChevronDown
              className={`size-3 text-slate-400 transition-transform ${activePanel === "filters" ? "rotate-180" : ""}`}
            />
          </button>

          {/* Sort */}
          <button
            type="button"
            onClick={() => togglePanel("sort")}
            className={`inline-flex cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
              activePanel === "sort" || hasActiveSort
                ? "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200"
                : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
            }`}
          >
            <ArrowDownAZ className="size-3.5" />
            Sort
            {hasActiveSort && (
              <span className="ml-0.5 px-1.5 py-0.5 rounded-full bg-indigo-600 text-white text-[10px] font-semibold">
                {sortRules.filter((r) => r.field).length}
              </span>
            )}
          </button>
        </div>

        {/* Active indicator pills + export */}
        <div className="flex items-center gap-1.5">
          {hasActiveFilters && (
            <button
              type="button"
              onClick={handleClearFilters}
              className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-700 hover:bg-indigo-100 transition-colors"
            >
              {Object.keys(filters).length} filter
              {Object.keys(filters).length !== 1 ? "s" : ""}
              <X className="size-3" />
            </button>
          )}
          {hasActiveSort && (
            <button
              type="button"
              onClick={handleClearSort}
              className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-700 hover:bg-indigo-100 transition-colors"
            >
              {sortRules.filter((r) => r.field).length} sort
              {sortRules.filter((r) => r.field).length !== 1 ? "s" : ""}
              <X className="size-3" />
            </button>
          )}
          {onExportCsv && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              onClick={onExportCsv}
              loading={isExporting}
              disabled={exportDisabled}
              startIcon={<Download className="size-3.5" />}
              title="Export all rows matching the current filters as CSV"
            >
              {isExporting
                ? exportProgress && exportProgress.total > 0
                  ? `Exporting ${exportProgress.fetched.toLocaleString()}/${exportProgress.total.toLocaleString()}`
                  : "Exporting…"
                : "Export CSV"}
            </Button>
          )}
        </div>
      </div>

      {/* Filters Panel (inline, below toolbar) */}
      {activePanel === "filters" && (
        <div className="border-t border-slate-100 px-3 py-2.5 bg-slate-50/50">
          <div className="flex items-center justify-end pb-2">
            <button
              type="button"
              onClick={addFilterRule}
              className="inline-flex cursor-pointer items-center gap-1 text-[11px] font-medium text-indigo-600 hover:text-indigo-800"
            >
              <Plus className="size-3" />
              Add rule
            </button>
          </div>

          <div className="flex-1 overflow-y-auto mt-1 max-h-[300px]">
            {filterRules.length === 0 ? (
              <div className="text-center py-6 text-xs text-slate-400">
                No filters. Click &ldquo;Add rule&rdquo; to get started.
              </div>
            ) : (
              <div className="space-y-1.5">
                {filterRules.map((rule) => (
                  <div
                    key={rule.id}
                    className="flex flex-col gap-1.5 rounded-md bg-white border border-slate-200 px-2.5 py-1.5"
                  >
                    <div className="flex items-center gap-2">
                      <div
                        className={`flex-1 grid gap-1.5 overflow-hidden ${fieldGroups.get(rule.originalField || "")?.requiresSubfield ? "grid-cols-[minmax(0,1fr)_minmax(0,1fr)_192px]" : "grid-cols-[minmax(0,1fr)_192px]"}`}
                      >
                        {(() => {
                          const group = fieldGroups.get(
                            rule.originalField || "",
                          );
                          const requiresSubfield =
                            group?.requiresSubfield || false;
                          const subcolumns = group?.subcolumns || [];
                          const dataType = requiresSubfield
                            ? subcolumns.find((s) => s.value === rule.subcolumn)
                                ?.dataType
                            : group?.defaultDataType;

                          return (
                            <>
                              <RichSelect
                                options={originalFieldOptions.map((f) => ({
                                  label: f.replace(/_/g, " "),
                                  value: f,
                                }))}
                                value={rule.originalField || ""}
                                onChange={(v) => {
                                  const newGroup = fieldGroups.get(v);
                                  let newFieldPath = rule.field;
                                  let newSubcolumn: string | undefined =
                                    undefined;
                                  let newDataType: DataType | undefined =
                                    undefined;

                                  if (newGroup) {
                                    if (
                                      newGroup.requiresSubfield &&
                                      newGroup.subcolumns.length > 0
                                    ) {
                                      newSubcolumn =
                                        newGroup.subcolumns[0].value;
                                      newFieldPath =
                                        newGroup.subcolumns[0].fieldPath;
                                      newDataType =
                                        newGroup.subcolumns[0].dataType;
                                    } else {
                                      newFieldPath = newGroup.defaultFieldPath;
                                      newDataType = newGroup.defaultDataType;
                                    }
                                  }

                                  updateFilterRule(rule.id, {
                                    originalField: v,
                                    subcolumn: newSubcolumn,
                                    field: newFieldPath,
                                    operator:
                                      getOperatorOptionsForField(newDataType)[0]
                                        ?.value || "eq",
                                    value: "",
                                  });
                                }}
                                size="sm"
                                placeholder="Field"
                                renderOption={(option, isSelected) => {
                                  const nested = fieldGroups.get(
                                    option.value,
                                  )?.requiresSubfield;
                                  return (
                                    <div
                                      className={`flex items-center gap-2 px-3 py-2 ${isSelected ? "text-indigo-700" : "text-slate-700"}`}
                                    >
                                      <span className="flex-1 text-sm truncate">
                                        {option.label}
                                      </span>
                                      {nested && (
                                        <Layers
                                          className={`size-3 shrink-0 ${isSelected ? "text-indigo-400" : "text-slate-400"}`}
                                        />
                                      )}
                                      {isSelected && (
                                        <Check className="size-3 shrink-0 text-indigo-600" />
                                      )}
                                    </div>
                                  );
                                }}
                                renderValue={(option) => {
                                  const nested = fieldGroups.get(
                                    option.value,
                                  )?.requiresSubfield;
                                  return (
                                    <div className="flex items-center gap-1.5 min-w-0">
                                      <span className="truncate">
                                        {option.label}
                                      </span>
                                      {nested && (
                                        <Layers className="size-3 shrink-0 text-slate-400" />
                                      )}
                                    </div>
                                  );
                                }}
                              />
                              {requiresSubfield && (
                                <RichSelect
                                  options={subcolumns.map((s) => ({
                                    label: s.label.replace(/_/g, " "),
                                    value: s.value,
                                  }))}
                                  value={rule.subcolumn || ""}
                                  onChange={(v) => {
                                    const subcol = subcolumns.find(
                                      (s) => s.value === v,
                                    );
                                    updateFilterRule(rule.id, {
                                      subcolumn: v,
                                      field: subcol?.fieldPath || rule.field,
                                      operator:
                                        getOperatorOptionsForField(
                                          subcol?.dataType,
                                        )[0]?.value || "eq",
                                      value: "",
                                    });
                                  }}
                                  size="sm"
                                  placeholder="Subfield"
                                />
                              )}
                              <RichSelect<FilterRule["operator"]>
                                options={
                                  getOperatorOptionsForField(
                                    dataType,
                                  ) as RichSelectOption<
                                    FilterRule["operator"]
                                  >[]
                                }
                                value={rule.operator}
                                onChange={(v) =>
                                  updateFilterRule(rule.id, {
                                    operator: v,
                                    value: "",
                                  })
                                }
                                size="sm"
                                className="min-w-48"
                                renderOption={(option, isSelected) => (
                                  <div
                                    className={`flex items-center gap-2 px-3 py-2 ${isSelected ? "text-indigo-700" : "text-slate-700"}`}
                                  >
                                    <span
                                      className={
                                        isSelected
                                          ? "text-indigo-600"
                                          : "text-slate-400"
                                      }
                                    >
                                      {OPERATOR_ICON_MAP[option.value]}
                                    </span>
                                    <span className="text-sm">
                                      {option.label}
                                    </span>
                                    {isSelected && (
                                      <Check className="ml-auto size-3 text-indigo-600" />
                                    )}
                                  </div>
                                )}
                                renderValue={(option) => (
                                  <div className="flex items-center gap-1.5">
                                    <span className="text-slate-400">
                                      {OPERATOR_ICON_MAP[option.value]}
                                    </span>
                                    <span>{option.label}</span>
                                  </div>
                                )}
                              />
                            </>
                          );
                        })()}
                      </div>
                      <button
                        type="button"
                        onClick={() => removeFilterRule(rule.id)}
                        className="p-0.5 rounded text-slate-400 hover:text-slate-600 hover:bg-slate-200 transition-colors shrink-0"
                      >
                        <X className="size-3.5" />
                      </button>
                    </div>

                    <div>
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
                          type="text"
                          placeholder="Value"
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
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="flex items-center justify-between pt-2 mt-2 border-t border-slate-100">
            <button
              type="button"
              onClick={() => {
                handleClearFilters();
                setActivePanel(null);
              }}
              disabled={!hasActiveFilters}
              className="text-[11px] font-medium text-slate-500 hover:text-slate-700 disabled:text-slate-300 disabled:cursor-default"
            >
              Clear all
            </button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                applyFilters();
                setActivePanel(null);
              }}
              className="h-7 text-xs"
            >
              Apply
            </Button>
          </div>
        </div>
      )}

      {/* Sort Panel (inline, below toolbar) */}
      {activePanel === "sort" && (
        <div className="border-t border-slate-100 px-3 py-2.5 bg-slate-50/50">
          {sortRules.length === 0 ? (
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400">
                No sort rules applied.
              </span>
              <button
                type="button"
                onClick={addSortRule}
                className="inline-flex cursor-pointer items-center gap-1 text-[11px] font-medium text-indigo-600 hover:text-indigo-800"
              >
                <Plus className="size-3" />
                Add sort
              </button>
            </div>
          ) : (
            <div className="space-y-1.5">
              {sortRules.map((rule, index) => (
                <div
                  key={index}
                  className="flex items-center gap-2 rounded-md bg-white border border-slate-200 px-2.5 py-1.5"
                >
                  <span className="flex items-center justify-center size-5 rounded-full bg-slate-100 text-[10px] font-semibold text-slate-500 shrink-0">
                    {index + 1}
                  </span>
                  <div
                    className={`flex-1 grid gap-1.5 ${sortFieldGroups.get(rule.originalField || "")?.requiresSubfield ? "grid-cols-2" : "grid-cols-1"}`}
                  >
                    {(() => {
                      const group = sortFieldGroups.get(
                        rule.originalField || "",
                      );
                      const requiresSubfield = group?.requiresSubfield || false;
                      const subcolumns = group?.subcolumns || [];

                      return (
                        <>
                          <RichSelect
                            options={sortOriginalFieldOptions.map((f) => ({
                              label: f.replace(/_/g, " "),
                              value: f,
                            }))}
                            value={rule.originalField || ""}
                            onChange={(v) => {
                              const newGroup = sortFieldGroups.get(v);
                              let newSortField = rule.field;
                              let newSubcolumn: string | undefined = undefined;

                              if (newGroup) {
                                if (
                                  newGroup.requiresSubfield &&
                                  newGroup.subcolumns.length > 0
                                ) {
                                  newSubcolumn = newGroup.subcolumns[0].value;
                                  newSortField =
                                    newGroup.subcolumns[0].sortField;
                                } else {
                                  newSortField =
                                    newGroup.defaultSortField || "";
                                }
                              }

                              updateSortRule(index, {
                                originalField: v,
                                subcolumn: newSubcolumn,
                                field: newSortField,
                              });
                            }}
                            size="sm"
                            placeholder="Field"
                            renderOption={(option, isSelected) => {
                              const nested = sortFieldGroups.get(
                                option.value,
                              )?.requiresSubfield;
                              return (
                                <div
                                  className={`flex items-center gap-2 px-3 py-2 ${isSelected ? "text-indigo-700" : "text-slate-700"}`}
                                >
                                  <span className="flex-1 text-sm truncate">
                                    {option.label}
                                  </span>
                                  {nested && (
                                    <Layers
                                      className={`size-3 shrink-0 ${isSelected ? "text-indigo-400" : "text-slate-400"}`}
                                    />
                                  )}
                                  {isSelected && (
                                    <Check className="size-3 shrink-0 text-indigo-600" />
                                  )}
                                </div>
                              );
                            }}
                            renderValue={(option) => {
                              const nested = sortFieldGroups.get(
                                option.value,
                              )?.requiresSubfield;
                              return (
                                <div className="flex items-center gap-1.5 min-w-0">
                                  <span className="truncate">
                                    {option.label}
                                  </span>
                                  {nested && (
                                    <Layers className="size-3 shrink-0 text-slate-400" />
                                  )}
                                </div>
                              );
                            }}
                          />
                          {requiresSubfield && (
                            <RichSelect
                              options={subcolumns.map((s) => ({
                                label: s.label,
                                value: s.value,
                              }))}
                              value={rule.subcolumn || ""}
                              onChange={(v) => {
                                const subcol = subcolumns.find(
                                  (s) => s.value === v,
                                );
                                updateSortRule(index, {
                                  subcolumn: v,
                                  field: subcol?.sortField || rule.field,
                                });
                              }}
                              size="sm"
                              placeholder="Subfield"
                            />
                          )}
                        </>
                      );
                    })()}
                  </div>
                  <div className="flex gap-0.5 shrink-0">
                    <button
                      type="button"
                      onClick={() => updateSortRule(index, { order: "asc" })}
                      className={`inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium transition-colors ${
                        rule.order === "asc"
                          ? "bg-indigo-600 text-white"
                          : "bg-slate-100 text-slate-500 hover:bg-slate-200"
                      }`}
                    >
                      <ArrowUpAZ className="size-3" />
                      Asc
                    </button>
                    <button
                      type="button"
                      onClick={() => updateSortRule(index, { order: "desc" })}
                      className={`inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium transition-colors ${
                        rule.order === "desc"
                          ? "bg-indigo-600 text-white"
                          : "bg-slate-100 text-slate-500 hover:bg-slate-200"
                      }`}
                    >
                      <ArrowDownAZ className="size-3" />
                      Desc
                    </button>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeSortRule(index)}
                    className="p-0.5 rounded text-slate-400 hover:text-slate-600 hover:bg-slate-200 transition-colors shrink-0"
                  >
                    <X className="size-3.5" />
                  </button>
                </div>
              ))}
              <div className="flex items-center justify-between pt-1">
                <button
                  type="button"
                  onClick={addSortRule}
                  className="inline-flex cursor-pointer items-center gap-1 text-[11px] font-medium text-indigo-600 hover:text-indigo-800"
                >
                  <Plus className="size-3" />
                  Add sort
                </button>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={applySort}
                  className="h-7 text-xs"
                >
                  Apply
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
