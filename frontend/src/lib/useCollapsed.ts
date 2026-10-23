"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * A panel's collapsed state, remembered across visits.
 *
 * Read from `localStorage` in an effect rather than in the initial state, and
 * never during the first render: these pages are prerendered, so seeding state
 * from storage would make the server's markup and the client's disagree on the
 * very first paint. Every panel opens expanded and closes itself on mount if
 * that is how it was left.
 */
const KEY_PREFIX = "di-collapsed:";

export function useCollapsed(key: string) {
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(KEY_PREFIX + key);
      if (stored !== null) setCollapsed(stored === "true");
    } catch {
      // Storage disabled: the panel simply opens expanded every time.
    }
  }, [key]);

  const toggle = useCallback(() => {
    setCollapsed((previous) => {
      const next = !previous;
      try {
        window.localStorage.setItem(KEY_PREFIX + key, next ? "true" : "false");
      } catch {
        // As above — the state still applies for this visit.
      }
      return next;
    });
  }, [key]);

  return { collapsed, toggle };
}

export default useCollapsed;
