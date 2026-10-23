import { useCallback, useEffect, useMemo, useState } from "react";

import { defaultTo, entries, filter, get, isPlainObject } from "lodash-es";

import type { ChunkDetailPayload } from "../types";
import { extractErrorMessage, hasRenderableValue } from "../utils";

interface UseChunkDetailOptions {
  fetchChunkMetadata?: (chunkId: string) => Promise<any>;
  selectedChunkId: string | null;
}

export const useChunkDetail = ({
  fetchChunkMetadata,
  selectedChunkId,
}: UseChunkDetailOptions) => {
  const [chunkDetails, setChunkDetails] = useState<
    Record<string, ChunkDetailPayload | null>
  >({});
  const [chunkDetailState, setChunkDetailState] = useState<
    Record<string, { loading: boolean; error: string | null }>
  >({});

  const loadChunkDetail = useCallback(
    async (chunkId: string) => {
      if (!fetchChunkMetadata || !chunkId) return;
      setChunkDetailState((prev) => ({
        ...prev,
        [chunkId]: { loading: true, error: null },
      }));
      try {
        const response = await fetchChunkMetadata(chunkId);
        const raw = defaultTo(response, {});
        const metadata =
          isPlainObject(raw) && "metadata" in raw
            ? defaultTo(get(raw, "metadata"), {})
            : isPlainObject(raw)
              ? raw
              : {};
        setChunkDetails((prev) => ({
          ...prev,
          [chunkId]: { metadata, raw },
        }));
        setChunkDetailState((prev) => ({
          ...prev,
          [chunkId]: { loading: false, error: null },
        }));
      } catch (error) {
        setChunkDetailState((prev) => ({
          ...prev,
          [chunkId]: {
            loading: false,
            error: extractErrorMessage(error, "Failed to fetch chunk metadata"),
          },
        }));
      }
    },
    [fetchChunkMetadata],
  );

  useEffect(() => {
    if (!selectedChunkId) return;
    const detail = chunkDetails[selectedChunkId];
    const state = chunkDetailState[selectedChunkId];
    if (!detail && !state?.loading && fetchChunkMetadata) {
      void loadChunkDetail(selectedChunkId);
    }
  }, [
    selectedChunkId,
    chunkDetails,
    chunkDetailState,
    fetchChunkMetadata,
    loadChunkDetail,
  ]);

  const selectedChunkDetail = selectedChunkId
    ? chunkDetails[selectedChunkId]
    : null;
  const selectedChunkDetailState = selectedChunkId
    ? chunkDetailState[selectedChunkId]
    : undefined;

  const chunkMetadataEntries = useMemo(() => {
    const metadata = get(selectedChunkDetail, "metadata");
    if (!metadata) return [];
    return filter(entries(metadata), ([, value]) => hasRenderableValue(value));
  }, [selectedChunkDetail]);

  return {
    chunkDetails,
    chunkDetailState,
    selectedChunkDetail,
    selectedChunkDetailState,
    chunkMetadataEntries,
    loadChunkDetail,
  };
};
