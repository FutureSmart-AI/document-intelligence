"use client";

import { useCallback, useMemo, useState } from "react";

import { CheckSquare, ChevronDown, Search, Square } from "lucide-react";

import { Button } from "@/platform/components/ui";
import type { ColumnSchema } from "@/platform/types/data-view";

interface ColumnSelectorProps {
  columns: ColumnSchema[];
  selectedColumns: string[];
  onColumnsChange: (columns: string[]) => void;
  onClear: () => void;
}

export const ColumnSelector = ({
  columns,
  selectedColumns,
  onColumnsChange,
  onClear,
}: ColumnSelectorProps) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");

  // Get unique original fields (top-level fields for API)
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

  const filteredFields = useMemo(() => {
    if (!searchTerm.trim()) return availableFields;
    const search = searchTerm.toLowerCase();
    return availableFields.filter(
      (field) =>
        field.name.toLowerCase().includes(search) ||
        field.displayName.toLowerCase().includes(search),
    );
  }, [availableFields, searchTerm]);

  const isFieldSelected = useCallback(
    (fieldName: string) => {
      return selectedColumns.includes(fieldName);
    },
    [selectedColumns],
  );

  const toggleField = useCallback(
    (fieldName: string) => {
      if (isFieldSelected(fieldName)) {
        onColumnsChange(selectedColumns.filter((col) => col !== fieldName));
      } else {
        onColumnsChange([...selectedColumns, fieldName]);
      }
    },
    [selectedColumns, isFieldSelected, onColumnsChange],
  );

  const selectAll = useCallback(() => {
    onColumnsChange(availableFields.map((f) => f.name));
  }, [availableFields, onColumnsChange]);

  const clearAll = useCallback(() => {
    onColumnsChange([]);
  }, [onColumnsChange]);

  const hasSelection = selectedColumns.length > 0;

  return (
    <div className="bg-white rounded-lg border border-slate-200 shadow-sm">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between p-4 hover:bg-slate-50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <CheckSquare className="size-4 text-slate-600" />
          <span className="font-medium text-slate-900">Column Selection</span>
          {hasSelection && (
            <span className="px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 text-xs font-medium">
              {selectedColumns.length} / {availableFields.length}
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
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-slate-400" />
            <input
              type="text"
              placeholder="Search fields..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full pl-9 pr-4 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={selectAll}>
              Select All
            </Button>
            <Button variant="outline" size="sm" onClick={clearAll}>
              Clear All
            </Button>
            {hasSelection && (
              <Button variant="outline" size="sm" onClick={onClear}>
                Reset to Default
              </Button>
            )}
          </div>

          <div className="max-h-96 overflow-y-auto border border-slate-200 rounded-lg p-4">
            {filteredFields.length === 0 ? (
              <div className="text-center py-8 text-slate-500 text-sm">
                No fields found matching "{searchTerm}"
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:grid-cols-3">
                {filteredFields.map((field) => {
                  const isSelected = isFieldSelected(field.name);
                  return (
                    <label
                      key={field.name}
                      className="flex items-start gap-2 cursor-pointer hover:bg-slate-50 p-2 rounded group"
                    >
                      <div className="mt-0.5">
                        {isSelected ? (
                          <CheckSquare className="size-4 text-blue-600" />
                        ) : (
                          <Square className="size-4 text-slate-400" />
                        )}
                      </div>
                      <div
                        className="flex-1 min-w-0"
                        onClick={() => toggleField(field.name)}
                      >
                        <div className="text-sm font-medium text-slate-900">
                          {field.displayName}
                        </div>
                        <div className="text-xs text-slate-500">
                          {field.dataTypes.join(", ")}
                        </div>
                      </div>
                    </label>
                  );
                })}
              </div>
            )}
          </div>

          {hasSelection && (
            <div className="pt-2 border-t border-slate-200">
              <p className="text-xs text-slate-500">
                Selected fields will be included in the API request. Object ID
                is always included.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
