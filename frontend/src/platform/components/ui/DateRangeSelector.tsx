import React, {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import dayjs, { type Dayjs } from "dayjs";
import { Calendar, ChevronLeft, ChevronRight, X } from "lucide-react";

import { cn } from "@/platform/utils";

import { DEFAULT_PRESETS, EMPTY_LIST, WEEKDAYS } from "./constants";

/* ------------------------------------------------------------------ */
/*  Public API                                                         */
/* ------------------------------------------------------------------ */

export interface DateRange {
  start: string; // YYYY-MM-DD
  end: string; // YYYY-MM-DD
}

export interface DateRangePreset {
  label: string;
  range: () => DateRange;
}

export interface DateRangeSelectorProps {
  /** Current value — `null` means no selection. */
  value: DateRange | null;
  /** Called when the user commits a new range (or clears). */
  onChange: (range: DateRange | null) => void;
  /** Placeholder shown when nothing is selected. */
  placeholder?: string;
  /** Quick-pick presets shown above the calendar. Pass `false` to hide. */
  presets?: DateRangePreset[] | false;
  /** Extra class on the root wrapper. */
  className?: string;
  /** Minimum selectable date (YYYY-MM-DD). */
  minDate?: string;
  /** Maximum selectable date (YYYY-MM-DD). */
  maxDate?: string;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const isSameDay = (a: Dayjs, b: Dayjs) => a.isSame(b, "day");
const isBetween = (d: Dayjs, start: Dayjs, end: Dayjs) =>
  (d.isAfter(start, "day") || d.isSame(start, "day")) &&
  (d.isBefore(end, "day") || d.isSame(end, "day"));

const formatDisplay = (range: DateRange): string => {
  const s = dayjs(range.start);
  const e = dayjs(range.end);
  if (isSameDay(s, e)) return s.format("MMM D, YYYY");
  if (s.year() === e.year()) {
    if (s.month() === e.month())
      return `${s.format("MMM D")} – ${e.format("D, YYYY")}`;
    return `${s.format("MMM D")} – ${e.format("MMM D, YYYY")}`;
  }
  return `${s.format("MMM D, YYYY")} – ${e.format("MMM D, YYYY")}`;
};

/* ------------------------------------------------------------------ */
/*  Mini calendar (single month)                                       */
/* ------------------------------------------------------------------ */

interface MiniCalendarProps {
  month: Dayjs;
  rangeStart: Dayjs | null;
  rangeEnd: Dayjs | null;
  hoverDate: Dayjs | null;
  onSelect: (d: Dayjs) => void;
  onHover: (d: Dayjs | null) => void;
  minDate?: Dayjs;
  maxDate?: Dayjs;
}

const MiniCalendar = memo(function MiniCalendar({
  month,
  rangeStart,
  rangeEnd,
  hoverDate,
  onSelect,
  onHover,
  minDate,
  maxDate,
}: MiniCalendarProps) {
  const firstDay = month.startOf("month");
  const daysInMonth = month.daysInMonth();
  const startOffset = firstDay.day(); // 0 = Sun

  // Determine visual range (use hover for preview when only start is picked)
  const visStart = rangeStart;
  let visEnd = rangeEnd;
  if (rangeStart && !rangeEnd && hoverDate) {
    visEnd = hoverDate.isBefore(rangeStart, "day") ? null : hoverDate;
  }

  const cells: React.ReactNode[] = [];

  // Empty cells for offset
  for (let i = 0; i < startOffset; i++) {
    cells.push(<div key={`e-${i}`} />);
  }

  for (let d = 1; d <= daysInMonth; d++) {
    const date = month.date(d);
    const isDisabled =
      (minDate && date.isBefore(minDate, "day")) ||
      (maxDate && date.isAfter(maxDate, "day"));
    const isToday = isSameDay(date, dayjs());
    const isStart = visStart ? isSameDay(date, visStart) : false;
    const isEnd = visEnd ? isSameDay(date, visEnd) : false;
    const inRange =
      visStart && visEnd ? isBetween(date, visStart, visEnd) : false;
    const isEdge = isStart || isEnd;

    cells.push(
      <button
        key={d}
        type="button"
        disabled={isDisabled}
        onClick={() => !isDisabled && onSelect(date)}
        onMouseEnter={() => !isDisabled && onHover(date)}
        className={cn(
          "relative cursor-pointer h-7 w-7 rounded-md text-xs font-medium transition-colors",
          isDisabled && "text-slate-300 cursor-not-allowed!",
          !isDisabled &&
            !isEdge &&
            !inRange &&
            "hover:bg-slate-100 text-slate-700",
          isToday && !isEdge && "font-bold text-indigo-600",
          inRange && !isEdge && "bg-indigo-50 text-indigo-700 rounded-none",
          isStart && "bg-indigo-600 text-white rounded-l-md rounded-r-none",
          isEnd && "bg-indigo-600 text-white rounded-r-md rounded-l-none",
          isStart && isEnd && "rounded-md",
        )}
      >
        {d}
      </button>,
    );
  }

  return (
    <div>
      {/* Weekday headers */}
      <div className="grid grid-cols-7 mb-1">
        {WEEKDAYS.map((wd) => (
          <div
            key={wd}
            className="h-7 w-7 flex items-center justify-center text-[10px] font-semibold text-slate-400 uppercase"
          >
            {wd}
          </div>
        ))}
      </div>
      {/* Day grid */}
      <div className="grid grid-cols-7" onMouseLeave={() => onHover(null)}>
        {cells}
      </div>
    </div>
  );
});

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

const DateRangeSelector = ({
  value,
  onChange,
  placeholder = "Select date range",
  presets: presetsProp,
  className,
  minDate: minDateStr,
  maxDate: maxDateStr,
}: DateRangeSelectorProps) => {
  const [open, setOpen] = useState(false);
  const [viewMonth, setViewMonth] = useState<Dayjs>(() =>
    value?.start
      ? dayjs(value.start).startOf("month")
      : dayjs().startOf("month"),
  );

  // Selection state within the popover (committed on "Apply" or preset click)
  const [pickStart, setPickStart] = useState<Dayjs | null>(
    value?.start ? dayjs(value.start) : null,
  );
  const [pickEnd, setPickEnd] = useState<Dayjs | null>(
    value?.end ? dayjs(value.end) : null,
  );
  const [hoverDate, setHoverDate] = useState<Dayjs | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [popoverAlign, setPopoverAlign] = useState<"left" | "right">("left");

  const presets =
    presetsProp === false ? EMPTY_LIST : (presetsProp ?? DEFAULT_PRESETS);
  const minDate = minDateStr ? dayjs(minDateStr) : undefined;
  const maxDate = maxDateStr ? dayjs(maxDateStr) : undefined;

  // Sync internal picks when external value changes while closed
  useEffect(() => {
    if (!open) {
      setPickStart(value?.start ? dayjs(value.start) : null);
      setPickEnd(value?.end ? dayjs(value.end) : null);
    }
  }, [value, open]);

  // Detect viewport overflow and adjust popover alignment
  useEffect(() => {
    if (!open || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const popoverWidth = 264; // matches the w-[264px] on the popover
    const spaceRight = window.innerWidth - rect.left;
    setPopoverAlign(spaceRight < popoverWidth + 16 ? "right" : "left");
  }, [open]);

  // Close on outside click
  useEffect(() => {
    if (!open) return undefined;
    const handler = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return undefined;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open]);

  const handleDayClick = useCallback(
    (d: Dayjs) => {
      if (!pickStart || (pickStart && pickEnd)) {
        // Start new selection
        setPickStart(d);
        setPickEnd(null);
      } else {
        // Complete selection
        if (d.isBefore(pickStart, "day")) {
          setPickEnd(pickStart);
          setPickStart(d);
        } else {
          setPickEnd(d);
        }
      }
    },
    [pickStart, pickEnd],
  );

  const commitRange = useCallback(
    (range: DateRange) => {
      onChange(range);
      setOpen(false);
    },
    [onChange],
  );

  const handleApply = useCallback(() => {
    if (pickStart && pickEnd) {
      commitRange({
        start: pickStart.format("YYYY-MM-DD"),
        end: pickEnd.format("YYYY-MM-DD"),
      });
    } else if (pickStart) {
      commitRange({
        start: pickStart.format("YYYY-MM-DD"),
        end: pickStart.format("YYYY-MM-DD"),
      });
    }
  }, [pickStart, pickEnd, commitRange]);

  const handlePreset = useCallback(
    (preset: DateRangePreset) => {
      const r = preset.range();
      setPickStart(dayjs(r.start));
      setPickEnd(dayjs(r.end));
      setViewMonth(dayjs(r.start).startOf("month"));
      commitRange(r);
    },
    [commitRange],
  );

  const handleClear = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onChange(null);
      setPickStart(null);
      setPickEnd(null);
      setOpen(false);
    },
    [onChange],
  );

  const prevMonth = useCallback(
    () => setViewMonth((m) => m.subtract(1, "month")),
    [],
  );
  const nextMonth = useCallback(
    () => setViewMonth((m) => m.add(1, "month")),
    [],
  );

  const canApply = Boolean(pickStart);
  const displayText = value ? formatDisplay(value) : null;

  // Check if a preset matches the current pick
  const activePresetLabel = useMemo(() => {
    if (!pickStart || !pickEnd) return null;
    const s = pickStart.format("YYYY-MM-DD");
    const e = pickEnd.format("YYYY-MM-DD");
    for (const p of presets) {
      const r = p.range();
      if (r.start === s && r.end === e) return p.label;
    }
    return null;
  }, [pickStart, pickEnd, presets]);

  return (
    <div ref={containerRef} className={cn("relative", className)}>
      {/* Trigger */}
      <button
        type="button"
        onClick={() => {
          if (!open) {
            setViewMonth(
              value?.start
                ? dayjs(value.start).startOf("month")
                : dayjs().startOf("month"),
            );
          }
          setOpen(!open);
        }}
        className={cn(
          "flex w-full cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-1.5 text-sm transition-colors",
          open
            ? "border-indigo-300 ring-2 ring-indigo-500/20 bg-white"
            : "border-slate-200 bg-slate-50 hover:bg-slate-100",
          displayText ? "text-slate-700" : "text-slate-600",
        )}
      >
        <Calendar className="size-3.5 shrink-0 text-slate-400" />
        <span className="flex-1 truncate text-left text-xs">
          {displayText || placeholder}
        </span>
        {value && (
          <span
            role="button"
            tabIndex={0}
            onClick={handleClear}
            onKeyDown={(e) => {
              if (e.key === "Enter")
                handleClear(e as unknown as React.MouseEvent);
            }}
            className="shrink-0 rounded-full p-0.5 text-slate-400 hover:bg-slate-200 hover:text-slate-600 transition-colors"
            aria-label="Clear date range"
          >
            <X className="size-3" />
          </span>
        )}
      </button>

      {/* Popover */}
      {open && (
        <div
          ref={popoverRef}
          className={cn(
            "absolute top-full z-50 mt-1 w-[264px] rounded-xl border border-slate-200 bg-white shadow-lg",
            popoverAlign === "right" ? "right-0" : "left-0",
          )}
        >
          {/* Presets */}
          {presets.length > 0 && (
            <div className="flex flex-wrap gap-1 border-b border-slate-100 px-3 py-2">
              {presets.map((p) => (
                <button
                  key={p.label}
                  type="button"
                  onClick={() => handlePreset(p)}
                  className={cn(
                    "rounded-full cursor-pointer px-2.5 py-1 text-[11px] font-medium transition-colors",
                    activePresetLabel === p.label
                      ? "bg-indigo-100 text-indigo-700"
                      : "bg-slate-100 text-slate-600 hover:bg-slate-200",
                  )}
                >
                  {p.label}
                </button>
              ))}
            </div>
          )}

          {/* Month navigation */}
          <div className="flex items-center justify-between px-3 pt-2.5 pb-1">
            <button
              type="button"
              onClick={prevMonth}
              className="rounded-md cursor-pointer p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition-colors"
              aria-label="Previous month"
            >
              <ChevronLeft className="size-4" />
            </button>
            <span className="text-xs font-semibold text-slate-700">
              {viewMonth.format("MMMM YYYY")}
            </span>
            <button
              type="button"
              onClick={nextMonth}
              className="rounded-md cursor-pointer p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition-colors"
              aria-label="Next month"
            >
              <ChevronRight className="size-4" />
            </button>
          </div>

          {/* Calendar */}
          <div className="px-3 pb-2">
            <MiniCalendar
              month={viewMonth}
              rangeStart={pickStart}
              rangeEnd={pickEnd}
              hoverDate={hoverDate}
              onSelect={handleDayClick}
              onHover={setHoverDate}
              minDate={minDate}
              maxDate={maxDate}
            />
          </div>

          {/* Footer */}
          <div className="flex items-center justify-between border-t border-slate-100 px-3 py-2">
            <span className="text-[11px] text-slate-400">
              {pickStart && pickEnd
                ? `${pickStart.format("MMM D")} - ${pickEnd.format("MMM D")}`
                : pickStart
                  ? `${pickStart.format("MMM D")} - ...`
                  : "Pick a start date"}
            </span>
            <button
              type="button"
              disabled={!canApply}
              onClick={handleApply}
              className="rounded-md cursor-pointer bg-indigo-600 px-3 py-1 text-[11px] font-medium text-white transition-colors hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Apply
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default memo(DateRangeSelector);
