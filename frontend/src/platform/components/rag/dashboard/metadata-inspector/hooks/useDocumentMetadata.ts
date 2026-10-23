import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { defaultTo, entries, filter } from "lodash-es";

import { extractErrorMessage, hasRenderableValue } from "../utils";

interface UseDocumentMetadataOptions {
  fetchDocumentMetadata?: (id: string) => Promise<Record<string, any>>;
  targetId: string | null;
  open: boolean;
}

export const useDocumentMetadata = ({
  fetchDocumentMetadata,
  targetId,
  open,
}: UseDocumentMetadataOptions) => {
  const [documentState, setDocumentState] = useState<{
    data: Record<string, any> | null;
    loading: boolean;
    error: string | null;
  }>({ data: null, loading: false, error: null });

  const activeIdRef = useRef<string | null>(null);

  const loadDocument = useCallback(
    async (id: string) => {
      if (!fetchDocumentMetadata) return;
      activeIdRef.current = id;
      setDocumentState({ data: null, loading: true, error: null });
      try {
        const response = await fetchDocumentMetadata(id);
        if (activeIdRef.current !== id) return;
        setDocumentState({
          data: defaultTo(response, {}),
          loading: false,
          error: null,
        });
      } catch (error) {
        if (activeIdRef.current !== id) return;
        setDocumentState({
          data: null,
          loading: false,
          error: extractErrorMessage(
            error,
            "Failed to fetch document metadata",
          ),
        });
      }
    },
    [fetchDocumentMetadata],
  );

  useEffect(() => {
    if (!open || !targetId) {
      setDocumentState({ data: null, loading: false, error: null });
      return;
    }
    if (fetchDocumentMetadata) {
      void loadDocument(targetId);
    }
  }, [open, targetId, fetchDocumentMetadata, loadDocument]);

  const documentMetadataEntries = useMemo(() => {
    if (!documentState.data) return [];
    return filter(entries(documentState.data), ([, value]) =>
      hasRenderableValue(value),
    );
  }, [documentState.data]);

  return {
    documentState,
    documentMetadataEntries,
    loadDocument,
  };
};
