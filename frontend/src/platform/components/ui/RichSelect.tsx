"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { ChevronDown } from "lucide-react";

import {
  DROPDOWN_TRIGGER_GAP,
  DROPDOWN_VIEWPORT_MARGIN,
  MIN_DROPDOWN_HEIGHT,
  MIN_DROPDOWN_SCROLL_HEIGHT,
} from "./constants";
import Label from "./Label";

export interface RichSelectOption<T = string> {
  label: string;
  value: T;
  disabled?: boolean;
}

export type RichSelectSize = "xs" | "sm" | "md" | "lg";

export interface RichSelectProps<T = string> {
  options: RichSelectOption<T>[];
  value?: T;
  onChange?: (value: T) => void;
  renderOption?: (
    option: RichSelectOption<T>,
    isSelected: boolean,
  ) => React.ReactNode;
  renderValue?: (option: RichSelectOption<T>) => React.ReactNode;
  placeholder?: string;
  size?: RichSelectSize;
  disabled?: boolean;
  error?: boolean;
  errorMessage?: string;
  fullWidth?: boolean;
  className?: string;
  label?: string | React.ReactNode;
}

const sizeClasses: Record<RichSelectSize, string> = {
  xs: "px-2 py-1 text-xs",
  sm: "px-2.5 py-1.5 text-sm",
  md: "px-3 py-2 text-sm",
  lg: "px-4 py-2.5 text-base",
};

interface DropdownPos {
  top?: number;
  bottom?: number;
  left: number;
  minWidth: number;
  maxHeight: number;
}

export function RichSelect<T = string>({
  options,
  value,
  onChange,
  renderOption,
  renderValue,
  placeholder,
  size = "md",
  disabled = false,
  error = false,
  errorMessage,
  fullWidth = true,
  className = "",
  label,
}: RichSelectProps<T>) {
  const [isOpen, setIsOpen] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState(-1);
  const [dropdownPos, setDropdownPos] = useState<DropdownPos | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dropdownRef = useRef<HTMLUListElement>(null);

  const selectedOption = options.find((o) => o.value === value) ?? null;

  const computePos = useCallback((): DropdownPos | null => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return null;
    const spaceBelow =
      window.innerHeight -
      rect.bottom -
      DROPDOWN_TRIGGER_GAP -
      DROPDOWN_VIEWPORT_MARGIN;
    const spaceAbove =
      rect.top - DROPDOWN_TRIGGER_GAP - DROPDOWN_VIEWPORT_MARGIN;
    // Flip up only when below genuinely cannot hold the menu and above is the
    // roomier side; otherwise the menu keeps its natural downward placement.
    const above = spaceBelow < MIN_DROPDOWN_HEIGHT && spaceAbove > spaceBelow;
    return {
      ...(above
        ? { bottom: window.innerHeight - rect.top + DROPDOWN_TRIGGER_GAP }
        : { top: rect.bottom + DROPDOWN_TRIGGER_GAP }),
      // Keep the menu inside the viewport for triggers near the right edge.
      left: Math.max(
        DROPDOWN_VIEWPORT_MARGIN,
        Math.min(
          rect.left,
          window.innerWidth - rect.width - DROPDOWN_VIEWPORT_MARGIN,
        ),
      ),
      minWidth: rect.width,
      // Whatever room is left becomes the scroll area, so the last option is
      // always reachable however cramped the space is.
      maxHeight: Math.max(
        above ? spaceAbove : spaceBelow,
        MIN_DROPDOWN_SCROLL_HEIGHT,
      ),
    };
  }, []);

  useEffect(() => {
    if (!isOpen) return undefined;
    const handler = (e: MouseEvent) => {
      if (
        !containerRef.current?.contains(e.target as Node) &&
        !dropdownRef.current?.contains(e.target as Node)
      ) {
        setIsOpen(false);
        setFocusedIndex(-1);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [isOpen]);

  // Keep dropdown position in sync when scrolling or resizing
  useEffect(() => {
    if (!isOpen) return undefined;
    const update = () => setDropdownPos(computePos());
    window.addEventListener("scroll", update, true);
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update, true);
      window.removeEventListener("resize", update);
    };
  }, [isOpen, computePos]);

  // The menu can now scroll, so keyboard navigation has to drag the focused
  // option into view with it.
  useEffect(() => {
    if (!isOpen || focusedIndex < 0) return;
    const item = dropdownRef.current?.children[focusedIndex] as
      HTMLElement | undefined;
    item?.scrollIntoView?.({ block: "nearest" });
  }, [isOpen, focusedIndex]);

  const open = useCallback(() => {
    if (disabled) return;
    const selectedIndex = options.findIndex((o) => o.value === value);
    setFocusedIndex(selectedIndex >= 0 ? selectedIndex : 0);
    setDropdownPos(computePos());
    setIsOpen(true);
  }, [disabled, options, value, computePos]);

  const close = useCallback(() => {
    setIsOpen(false);
    setFocusedIndex(-1);
    triggerRef.current?.focus();
  }, []);

  const select = useCallback(
    (option: RichSelectOption<T>) => {
      if (option.disabled) return;
      onChange?.(option.value);
      close();
    },
    [onChange, close],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (!isOpen) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        close();
      } else if (e.key === "Tab") {
        close(); // no preventDefault — let focus advance naturally
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setFocusedIndex((prev) => (prev + 1 >= options.length ? 0 : prev + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setFocusedIndex((prev) =>
          prev - 1 < 0 ? options.length - 1 : prev - 1,
        );
      } else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (focusedIndex >= 0 && focusedIndex < options.length) {
          select(options[focusedIndex]);
        }
      }
    },
    [isOpen, options, focusedIndex, open, close, select],
  );

  const triggerBorderClass = error
    ? "border-rose-500 focus:ring-rose-500/40 focus:border-rose-500"
    : "border-slate-300 focus:ring-indigo-500/40 focus:border-indigo-500";

  const dropdown =
    isOpen && dropdownPos
      ? createPortal(
          <ul
            ref={dropdownRef}
            role="listbox"
            style={{
              position: "fixed",
              top: dropdownPos.top,
              bottom: dropdownPos.bottom,
              left: dropdownPos.left,
              minWidth: dropdownPos.minWidth,
              maxHeight: dropdownPos.maxHeight,
              zIndex: 9999,
            }}
            className="overflow-y-auto overscroll-contain rounded-lg border border-slate-200 bg-white py-1 shadow-lg"
          >
            {options.map((option, index) => {
              const isSelected = option.value === value;
              const isFocused = index === focusedIndex;
              return (
                <li
                  key={String(option.value)}
                  role="option"
                  aria-selected={isSelected}
                  aria-disabled={option.disabled}
                  className={[
                    "cursor-pointer select-none",
                    option.disabled ? "pointer-events-none opacity-40" : "",
                    isFocused && !isSelected ? "bg-slate-50" : "",
                    !renderOption && isSelected
                      ? "bg-indigo-50 text-indigo-700"
                      : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  onMouseEnter={() => setFocusedIndex(index)}
                  onClick={() => select(option)}
                >
                  {renderOption ? (
                    renderOption(option, isSelected)
                  ) : (
                    <span className="block px-3 py-2 text-sm">
                      {option.label}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>,
          document.body,
        )
      : null;

  return (
    <div
      ref={containerRef}
      className={`${fullWidth ? "w-full" : "inline-block"} ${className}`.trim()}
    >
      {label && <Label>{label}</Label>}
      <div className="relative">
        <button
          ref={triggerRef}
          type="button"
          role="combobox"
          aria-haspopup="listbox"
          aria-expanded={isOpen}
          disabled={disabled}
          className={[
            "flex items-center justify-between rounded-md border bg-white shadow-sm",
            fullWidth ? "w-full" : "",
            "cursor-pointer transition-colors focus:outline-none focus:ring-2",
            "disabled:cursor-not-allowed disabled:opacity-60",
            "pr-8",
            sizeClasses[size],
            triggerBorderClass,
          ].join(" ")}
          onClick={() => (isOpen ? close() : open())}
          onKeyDown={handleKeyDown}
        >
          <span className="flex-1 truncate text-left">
            {selectedOption ? (
              renderValue ? (
                renderValue(selectedOption)
              ) : (
                selectedOption.label
              )
            ) : (
              <span className="text-slate-400">{placeholder ?? ""}</span>
            )}
          </span>
          <ChevronDown
            className={[
              "pointer-events-none absolute right-2 top-1/2 size-4 -translate-y-1/2 transition-transform",
              isOpen ? "rotate-180" : "",
              error ? "text-rose-500" : "text-slate-400",
            ].join(" ")}
            strokeWidth={2.5}
            aria-hidden="true"
          />
        </button>

        {dropdown}
      </div>

      {error && errorMessage && (
        <p className="mt-1 text-xs text-rose-600">{errorMessage}</p>
      )}
    </div>
  );
}
