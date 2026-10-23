import { useCallback, useEffect, useState } from "react";

export interface IncrementalListResult<T> {
  visibleItems: T[];
  count: number;
  hasMore: boolean;
  loadMore: () => void;
  reset: () => void;
}

/**
 * Growing-window view over `items`. Reveals `initial` items, then `step` more on
 * each `loadMore()`. Resets to `initial` whenever the `items` reference changes
 * (e.g. a new filtered list). `count` is capped at `items.length`, so an
 * over-incremented internal count is harmless.
 *
 * Callers should pass a referentially-stable `items` (e.g. a `useMemo` result),
 * since a new reference each render is treated as a new list and resets the window.
 */
export function useIncrementalList<T>(
  items: T[],
  initial = 4,
  step = 4,
): IncrementalListResult<T> {
  const [count, setCount] = useState(initial);

  // Reset to the initial window whenever the source list changes identity.
  useEffect(() => {
    setCount(initial);
  }, [items, initial]);

  const cappedCount = Math.min(count, items.length);

  const loadMore = useCallback(() => setCount((c) => c + step), [step]);
  const reset = useCallback(() => setCount(initial), [initial]);

  return {
    visibleItems: items.slice(0, cappedCount),
    count: cappedCount,
    hasMore: cappedCount < items.length,
    loadMore,
    reset,
  };
}
