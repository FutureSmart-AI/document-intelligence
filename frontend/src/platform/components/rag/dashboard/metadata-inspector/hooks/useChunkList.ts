import { useCallback, useEffect, useState } from "react";

import type { ChunkSummary } from "../types";
import { extractErrorMessage } from "../utils";

interface UseChunkListOptions {
  fetchChunkList?: (id: string) => Promise<ChunkSummary[]>;
  targetId: string | null;
  open: boolean;
}

export const useChunkList = ({
  fetchChunkList,
  targetId,
  open,
}: UseChunkListOptions) => {
  const [chunkListState, setChunkListState] = useState<{
    items: ChunkSummary[];
    loading: boolean;
    error: string | null;
  }>({ items: [], loading: false, error: null });

  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(null);

  const loadChunkList = useCallback(
    async (id: string) => {
      if (!fetchChunkList) return;
      setChunkListState({ items: [], loading: true, error: null });
      setSelectedChunkId(null);
      try {
        const list = await fetchChunkList(id);
        setChunkListState({ items: list ?? [], loading: false, error: null });
        if (list && list.length > 0) {
          setSelectedChunkId(list[0].id);
        }
      } catch (error) {
        setChunkListState({
          items: [],
          loading: false,
          error: extractErrorMessage(error, "Failed to fetch chunks"),
        });
      }
    },
    [fetchChunkList],
  );

  useEffect(() => {
    if (!open || !targetId) {
      setChunkListState({ items: [], loading: false, error: null });
      setSelectedChunkId(null);
      return;
    }
    if (fetchChunkList) {
      void loadChunkList(targetId);
    }
  }, [open, targetId, fetchChunkList, loadChunkList]);

  return {
    chunkListState,
    selectedChunkId,
    setSelectedChunkId,
    loadChunkList,
  };
};
