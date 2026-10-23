"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Check, ChevronDown, Search } from "lucide-react";

import Button from "./Button";

export interface MultiSelectOption {
  label: string;
  value: string;
  description?: string;
}

export interface MultiSelectProps {
  options: MultiSelectOption[];
  selectedValues: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  maxHeight?: string;
  icon?: React.ReactNode;
  buttonVariant?: "outline" | "primary" | "secondary" | "ghost";
  size?: "sm" | "md" | "lg";
}

export const MultiSelect = ({
  options,
  selectedValues,
  onChange,
  placeholder = "Select items",
  searchPlaceholder = "Search...",
  maxHeight = "20rem",
  icon,
  buttonVariant = "outline",
  size = "sm",
}: MultiSelectProps) => {
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);

  const filteredOptions = useMemo(() => {
    if (!searchTerm.trim()) return options;
    const search = searchTerm.toLowerCase();
    return options.filter(
      (option) =>
        option.label.toLowerCase().includes(search) ||
        option.value.toLowerCase().includes(search) ||
        option.description?.toLowerCase().includes(search),
    );
  }, [options, searchTerm]);

  const toggleOption = useCallback(
    (value: string) => {
      if (selectedValues.includes(value)) {
        onChange(selectedValues.filter((v) => v !== value));
      } else {
        onChange([...selectedValues, value]);
      }
    },
    [selectedValues, onChange],
  );

  const selectAll = useCallback(() => {
    onChange(options.map((o) => o.value));
  }, [options, onChange]);

  const clearAll = useCallback(() => {
    onChange([]);
  }, [onChange]);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
        setSearchTerm("");
      }
    };

    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
      return () =>
        document.removeEventListener("mousedown", handleClickOutside);
    }
    return undefined;
  }, [isOpen]);

  const selectedCount = selectedValues.length;
  const buttonLabel =
    selectedCount === 0
      ? placeholder
      : selectedCount === options.length
        ? "All selected"
        : `${selectedCount} selected`;

  return (
    <div ref={containerRef} className="relative">
      <Button
        variant={buttonVariant}
        size={size}
        onClick={() => setIsOpen(!isOpen)}
        startIcon={icon}
        endIcon={
          <ChevronDown
            className={`size-4 transition-transform ${isOpen ? "rotate-180" : ""}`}
          />
        }
        className="gap-2"
      >
        {buttonLabel}
        {selectedCount > 0 && (
          <span className="ml-1 px-1.5 py-0.5 rounded-full bg-blue-600 text-white text-xs font-medium">
            {selectedCount}
          </span>
        )}
      </Button>

      {isOpen && (
        <div
          className="absolute z-50 mt-2 w-80 bg-white rounded-lg border border-slate-200 shadow-xl"
          style={{ maxHeight }}
        >
          {/* Search and actions */}
          <div className="p-3 border-b border-slate-200 space-y-2">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-slate-400" />
              <input
                type="text"
                placeholder={searchPlaceholder}
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-full pl-9 pr-4 py-1.5 text-sm border border-slate-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                autoFocus
              />
            </div>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <button
                  onClick={selectAll}
                  className="text-xs text-blue-600 hover:text-blue-700 font-medium"
                >
                  Select All
                </button>
                <span className="text-slate-300">|</span>
                <button
                  onClick={clearAll}
                  className="text-xs text-slate-600 hover:text-slate-700 font-medium"
                >
                  Clear
                </button>
              </div>
              <span className="text-xs text-slate-500">
                {selectedCount} / {options.length}
              </span>
            </div>
          </div>

          {/* Options list */}
          <div
            className="overflow-y-auto"
            style={{ maxHeight: "calc(20rem - 6rem)" }}
          >
            {filteredOptions.length === 0 ? (
              <div className="p-6 text-center text-sm text-slate-500">
                No items found matching "{searchTerm}"
              </div>
            ) : (
              <div className="p-2">
                {filteredOptions.map((option) => {
                  const isSelected = selectedValues.includes(option.value);
                  return (
                    <button
                      key={option.value}
                      onClick={() => toggleOption(option.value)}
                      className="w-full flex items-start gap-3 px-3 py-2 rounded-md hover:bg-slate-50 transition-colors text-left"
                    >
                      <div
                        className={`flex items-center justify-center w-4 h-4 mt-0.5 rounded border-2 flex-shrink-0 transition-colors ${
                          isSelected
                            ? "bg-blue-600 border-blue-600"
                            : "border-slate-300 hover:border-slate-400"
                        }`}
                      >
                        {isSelected && (
                          <Check
                            className="size-3 text-white"
                            strokeWidth={3}
                          />
                        )}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-slate-900">
                          {option.label}
                        </div>
                        {option.description && (
                          <div className="text-xs text-slate-500 mt-0.5">
                            {option.description}
                          </div>
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default MultiSelect;
