import { useEffect, useRef } from "react";

import { KnowledgeItem } from "@/platform/hooks/useKnowledgeData";

import { hasInProgressItems } from "../utils/fileUtils";

interface UseAutoRefreshOptions {
  allData?: KnowledgeItem[];
  refetch: () => void;
  enabled?: boolean;
  intervalMs?: number;
}

const INTERVAL_MS = 35000; // 35 seconds (middle of 30-45 range)

/**
 * Custom hook that automatically refreshes knowledge data when there are
 * items with in-progress status in the unified data list.
 *
 * Stage 1: When all items are completed/failed, no refresh happens
 * Stage 2: When any item is in progress, refreshes every ~35 seconds
 */
export const useAutoRefresh = ({
  allData,
  refetch,
  enabled = true,
  intervalMs = INTERVAL_MS,
}: UseAutoRefreshOptions) => {
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    // Clear any existing interval
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }

    // Don't set up polling if disabled
    if (!enabled) {
      return () => {};
    }

    // Check if there are any in-progress items
    const hasInProgress = hasInProgressItems(allData);

    // Stage 1: If all items are completed/failed, don't refresh
    if (!hasInProgress) {
      return () => {};
    }

    // Stage 2: If there are in-progress items, set up auto-refresh
    intervalRef.current = setInterval(() => {
      refetch();
    }, intervalMs);

    // Cleanup on unmount or when dependencies change
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [allData, refetch, enabled, intervalMs]);
};
