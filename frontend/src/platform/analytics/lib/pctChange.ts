export interface PctChange {
  /** Signed percentage change, e.g. +5.2 or -3.1. */
  pct: number;
  /** Absolute value rounded to 1 decimal, ready to render (e.g. 5.2). */
  abs: number;
  direction: "up" | "down" | "flat";
}

/**
 * Relative percentage change of a metric vs its previous-period value.
 * `delta` is (current − previous); `prev` is the previous-period value.
 * Returns null when there's no baseline to compare against (missing delta or
 * a zero/absent previous value), so cards show the number without a % pill.
 */
export function pctChange(
  delta?: number | null,
  prev?: number | null,
): PctChange | null {
  if (delta == null || prev == null || prev === 0) return null;
  const pct = (delta / prev) * 100;
  return {
    pct,
    abs: Math.abs(Number(pct.toFixed(1))),
    direction: pct === 0 ? "flat" : pct > 0 ? "up" : "down",
  };
}
