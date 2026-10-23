"use client";

import { useMemo } from "react";

import { ChunkMetadataSection } from "./ChunkMetadataSection";
import { DEFAULT_CHUNK_CONFIG, DEFAULT_DOCUMENT_CONFIG } from "./constants";
import { DocumentMetadataSection } from "./DocumentMetadataSection";
import { useChunkDetail } from "./hooks/useChunkDetail";
import { useChunkList } from "./hooks/useChunkList";
import { useDocumentMetadata } from "./hooks/useDocumentMetadata";
import type { ChunkSummary, MetadataSectionConfig } from "./types";

interface InlineMetadataInspectorProps {
  targetId: string | null;
  fetchDocumentMetadata?: (id: string) => Promise<Record<string, any>>;
  fetchChunkList?: (id: string) => Promise<ChunkSummary[]>;
  fetchChunkMetadata?: (chunkId: string) => Promise<any>;
  documentConfig?: MetadataSectionConfig;
  chunkConfig?: MetadataSectionConfig;
}

export const InlineMetadataInspector = ({
  targetId,
  fetchDocumentMetadata,
  fetchChunkList,
  fetchChunkMetadata,
  documentConfig = DEFAULT_DOCUMENT_CONFIG,
  chunkConfig = DEFAULT_CHUNK_CONFIG,
}: InlineMetadataInspectorProps) => {
  const { documentState, documentMetadataEntries, loadDocument } =
    useDocumentMetadata({
      fetchDocumentMetadata,
      targetId,
      open: true,
    });

  const { chunkListState, selectedChunkId, setSelectedChunkId, loadChunkList } =
    useChunkList({
      fetchChunkList,
      targetId,
      open: true,
    });

  const {
    selectedChunkDetail,
    selectedChunkDetailState,
    chunkMetadataEntries,
    loadChunkDetail,
  } = useChunkDetail({
    fetchChunkMetadata,
    selectedChunkId,
  });

  const selectedChunkSummary = useMemo(() => {
    return (
      chunkListState.items.find((item) => item.id === selectedChunkId) ?? null
    );
  }, [chunkListState.items, selectedChunkId]);

  const hasDocumentSection = Boolean(fetchDocumentMetadata);
  const hasChunkSection = Boolean(fetchChunkList);

  if (!hasDocumentSection && !hasChunkSection) {
    return (
      <p className="text-sm text-slate-500">No metadata sections available.</p>
    );
  }

  return (
    <div className="space-y-8">
      {hasDocumentSection && (
        <DocumentMetadataSection
          config={documentConfig}
          documentState={documentState}
          documentMetadataEntries={documentMetadataEntries}
          targetId={targetId}
          onRetry={() => targetId && loadDocument(targetId)}
        />
      )}

      {hasDocumentSection && hasChunkSection && (
        <hr className="border-slate-200" />
      )}

      {hasChunkSection && (
        <ChunkMetadataSection
          config={chunkConfig}
          chunkListState={chunkListState}
          selectedChunkId={selectedChunkId}
          selectedChunkSummary={selectedChunkSummary}
          selectedChunkDetail={selectedChunkDetail}
          selectedChunkDetailState={selectedChunkDetailState}
          chunkMetadataEntries={chunkMetadataEntries}
          targetId={targetId}
          onChunkSelect={setSelectedChunkId}
          onRetryList={() => targetId && loadChunkList(targetId)}
          onRetryDetail={loadChunkDetail}
        />
      )}
    </div>
  );
};
