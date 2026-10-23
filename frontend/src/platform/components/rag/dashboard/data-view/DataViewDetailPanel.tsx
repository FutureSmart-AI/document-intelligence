"use client";

import { useCallback } from "react";

import { CopyToClipboard } from "@/platform/components/conversations/copy-to-clipboard";
import { InlineMetadataInspector } from "@/platform/components/rag/dashboard/metadata-inspector/InlineMetadataInspector";
import { PreviewHeaderControls } from "@/platform/components/rag/dashboard/preview";
import { BackButton } from "@/platform/components/ui";
import { useAuth } from "@/platform/hooks/useAuth";
import { cn } from "@/platform/utils";
import {
  getDocumentChunkMetadata,
  getDocumentChunks,
  getDocumentMetadata,
} from "@/platform/utils/axios";

interface DataViewDetailPanelProps {
  objectId: string | null;
  /** "page" renders the standalone route chrome (Back button, wide layout);
   * "drawer" renders compact chrome for the right-side preview panel. */
  variant: "page" | "drawer";
  onClose: () => void;
  /** Drawer only: step through the list the panel was opened from. */
  onPrev?: () => void;
  onNext?: () => void;
  hasPrev?: boolean;
  hasNext?: boolean;
  positionLabel?: string;
  /** Drawer only: full-screen toggle. */
  expanded?: boolean;
  onToggleExpanded?: () => void;
}

export const DataViewDetailPanel = ({
  objectId,
  variant,
  onClose,
  onPrev,
  onNext,
  hasPrev,
  hasNext,
  positionLabel,
  expanded,
  onToggleExpanded,
}: DataViewDetailPanelProps) => {
  const { knowledgeId } = useAuth();

  const fetchDocumentMetadata = useCallback(
    async (docId: string) => {
      if (!docId || !knowledgeId) return {};
      const response = await getDocumentMetadata({
        object_id: docId,
        knowledgeId,
      });
      return response?.metadata || {};
    },
    [knowledgeId],
  );

  const fetchChunkList = useCallback(
    async (docId: string) => {
      if (!docId || !knowledgeId) return [];
      const response = await getDocumentChunks({
        object_id: docId,
        knowledgeId,
      });
      return response?.chunks || [];
    },
    [knowledgeId],
  );

  const fetchChunkMetadata = useCallback(
    async (chunkId: string) => {
      if (!chunkId || !knowledgeId) return null;
      const response = await getDocumentChunkMetadata({
        chunk_id: chunkId,
        knowledgeId,
      });
      return response;
    },
    [knowledgeId],
  );

  const HeaderLeftControl =
    variant === "page" ? <BackButton onClick={onClose} /> : null;

  const HeaderControls =
    variant === "drawer" ? (
      <PreviewHeaderControls
        onClose={onClose}
        onPrev={onPrev}
        onNext={onNext}
        hasPrev={hasPrev}
        hasNext={hasNext}
        positionLabel={positionLabel}
        expanded={expanded}
        onToggleExpanded={onToggleExpanded}
      />
    ) : null;

  if (!objectId) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex shrink-0 items-center justify-between gap-4 border-b border-slate-200 bg-white px-6 py-4">
          <div className="flex min-w-0 flex-1 items-center gap-4">
            {HeaderLeftControl}
            <h1 className="truncate text-xl font-semibold text-slate-900">
              Document Details
            </h1>
          </div>
          {HeaderControls}
        </div>
        <div className="flex flex-1 items-center justify-center">
          <div className="text-center text-slate-500">
            <p className="text-lg font-medium">Document not found</p>
            <p className="mt-1 text-sm">
              No document ID was provided. Please select a document from the
              Data View.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-4 border-b border-slate-200 bg-white px-6 py-4">
        <div className="flex min-w-0 flex-1 items-center gap-4">
          {HeaderLeftControl}
          <h1 className="truncate text-xl font-semibold text-slate-900">
            Document Details
          </h1>
        </div>
        {HeaderControls}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
        <div
          className={cn(
            "space-y-6",
            // Docked the panel is narrow, so the body uses its full width;
            // full-screen and the standalone page cap the reading width.
            (variant === "page" || expanded) && "max-w-5xl",
            expanded && "mx-auto",
          )}
        >
          {/* Object ID */}
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <span className="font-medium w-24">Object ID:</span>
            <CopyToClipboard
              name={objectId}
              tooltip="Copy Object ID"
              className="text-xs"
            >
              {objectId}
            </CopyToClipboard>
          </div>

          {/* Document & Chunk Metadata */}
          <div className="rounded-xl border border-slate-200 bg-white">
            <div className="border-b border-slate-100 px-5 py-3">
              <h2 className="text-sm font-semibold text-slate-900">
                Document &amp; Chunk Metadata
              </h2>
              <p className="mt-0.5 text-xs text-slate-500">
                Inspect document-level attributes and individual chunk details.
              </p>
            </div>
            <div className="px-5 py-5">
              <InlineMetadataInspector
                targetId={objectId}
                fetchDocumentMetadata={fetchDocumentMetadata}
                fetchChunkList={fetchChunkList}
                fetchChunkMetadata={fetchChunkMetadata}
                documentConfig={{
                  title: "Document Overview",
                  description: "Extracted metadata for this document.",
                  emptyMessage: "No document metadata available yet.",
                  loadingMessage: "Loading document metadata...",
                  rawJsonLabel: "View raw document JSON",
                }}
                chunkConfig={{
                  title: "Chunk Insights",
                  description:
                    "Inspect each processed chunk with summaries, topics, keywords, and raw payloads.",
                  emptyMessage: "No chunk-level metadata available yet.",
                  loadingMessage: "Loading chunk metadata...",
                  rawJsonLabel: "View raw chunk JSON",
                }}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
