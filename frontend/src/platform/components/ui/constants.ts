import dayjs from "dayjs";

/* ------------------------------------------------------------------ */
/*  Default presets                                                    */
/* ------------------------------------------------------------------ */

export const DEFAULT_PRESETS = [
  {
    label: "Last 7 Days",
    range: () => ({
      start: dayjs().subtract(7, "day").format("YYYY-MM-DD"),
      end: dayjs().format("YYYY-MM-DD"),
    }),
  },
  {
    label: "Last 30 Days",
    range: () => ({
      start: dayjs().subtract(30, "day").format("YYYY-MM-DD"),
      end: dayjs().format("YYYY-MM-DD"),
    }),
  },
  {
    label: "Last 90 Days",
    range: () => ({
      start: dayjs().subtract(90, "day").format("YYYY-MM-DD"),
      end: dayjs().format("YYYY-MM-DD"),
    }),
  },
  {
    label: "All",
    range: () => ({
      start: "2024-01-01",
      end: dayjs().format("YYYY-MM-DD"),
    }),
  },
];

export const WEEKDAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

export const EMPTY_LIST = [];

export const MAX_WIDTH_CLASSES: Record<string, string> = {
  md: "max-w-md",
  lg: "max-w-lg",
  xl: "max-w-xl",
  "2xl": "max-w-2xl",
  "3xl": "max-w-3xl",
  "4xl": "max-w-4xl",
  "5xl": "max-w-5xl",
  "6xl": "max-w-6xl",
};

export const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export const TOAST_POSITIONS = [
  "top-left",
  "top-center",
  "top-right",
  "bottom-left",
  "bottom-center",
  "bottom-right",
] as const;

export type ToastPosition = (typeof TOAST_POSITIONS)[number];

export const DEFAULT_POSITION: ToastPosition = "top-center";

export const POSITION_STYLES: Record<
  ToastPosition,
  { container: string; direction: string }
> = {
  "top-left": { container: "top-4 left-4 items-start", direction: "flex-col" },
  "top-center": {
    container: "top-4 left-1/2 -translate-x-1/2 items-center",
    direction: "flex-col",
  },
  "top-right": { container: "top-4 right-4 items-end", direction: "flex-col" },
  "bottom-left": {
    container: "bottom-4 left-4 items-start",
    direction: "flex-col-reverse",
  },
  "bottom-center": {
    container: "bottom-4 left-1/2 -translate-x-1/2 items-center",
    direction: "flex-col-reverse",
  },
  "bottom-right": {
    container: "bottom-4 right-4 items-end",
    direction: "flex-col-reverse",
  },
};

export const DEFAULT_PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

/* ------------------------------------------------------------------ */
/*  RichSelect dropdown placement                                      */
/* ------------------------------------------------------------------ */

/** Gap between a RichSelect trigger and its menu. */
export const DROPDOWN_TRIGGER_GAP = 4;

/** Breathing room kept between a RichSelect menu and the viewport edge. */
export const DROPDOWN_VIEWPORT_MARGIN = 8;

/**
 * Room for roughly two rich options. With less than this below the trigger the
 * menu opens upwards instead of unfolding off the bottom of the screen.
 */
export const MIN_DROPDOWN_HEIGHT = 140;

/** Floor for the menu's scroll area when neither side has much room. */
export const MIN_DROPDOWN_SCROLL_HEIGHT = 96;
