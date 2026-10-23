"use client";

import { map, startCase } from "lodash-es";
import { ChevronDownIcon, Layers, Loader, RefreshCw } from "lucide-react";
import {
  Panel,
  Group as PanelGroup,
  Separator as PanelResizeHandle,
} from "react-resizable-panels";

import { Markdown } from "@/platform/components/conversations/markdown";
import Button from "@/platform/components/ui/Button";

import { JsonViewer } from "./JsonViewer";
import { renderMetadataValue } from "./MetadataValueRenderer";
import type {
  ChunkDetailPayload,
  ChunkSummary,
  MetadataSectionConfig,
} from "./types";

interface ChunkMetadataSectionProps {
  config: MetadataSectionConfig;
  chunkListState: {
    items: ChunkSummary[];
    loading: boolean;
    error: string | null;
  };
  selectedChunkId: string | null;
  selectedChunkSummary: ChunkSummary | null;
  selectedChunkDetail: ChunkDetailPayload | null;
  selectedChunkDetailState?: { loading: boolean; error: string | null };
  chunkMetadataEntries: Array<[string, any]>;
  targetId: string | null;
  onChunkSelect: (chunkId: string | null) => void;
  onRetryList: () => void;
  onRetryDetail: (chunkId: string) => void;
}

export const ChunkMetadataSection = ({
  config,
  chunkListState,
  selectedChunkId,
  selectedChunkSummary,
  selectedChunkDetail,
  selectedChunkDetailState,
  chunkMetadataEntries,
  targetId,
  onChunkSelect,
  onRetryList,
  onRetryDetail,
}: ChunkMetadataSectionProps) => {
  return (
    <section>
      <div className="flex items-center justify-between pb-3">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-semibold text-slate-900">
            {config.title}
          </h3>
          <span
            className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
              chunkListState.error
                ? "bg-rose-50 text-rose-700 ring-rose-200"
                : "bg-violet-50 text-violet-700 ring-violet-200"
            }`}
          >
            {chunkListState.error
              ? "Error"
              : chunkListState.loading
                ? "Loading\u2026"
                : `${chunkListState.items.length} chunks`}
          </span>
        </div>
        {chunkListState.error && targetId ? (
          <Button
            variant="outline"
            size="sm"
            onClick={onRetryList}
            startIcon={<RefreshCw className="h-3.5 w-3.5" />}
          >
            Retry
          </Button>
        ) : null}
      </div>

      {config.description ? (
        <p className="pb-3 text-xs text-slate-500">{config.description}</p>
      ) : null}

      {chunkListState.loading ? (
        <div className="flex items-center gap-2 rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-sm text-slate-500">
          <Loader className="h-4 w-4 animate-spin text-violet-500" />
          {config.loadingMessage}
        </div>
      ) : chunkListState.error ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          {chunkListState.error}
        </div>
      ) : chunkListState.items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-center text-sm text-slate-500">
          {config.emptyMessage}
        </div>
      ) : (
        <div className="overflow-hidden resize-y rounded-lg border border-slate-200 min-h-[320px] max-h-[1200px] h-[400px]">
          <PanelGroup
            orientation="horizontal"
            className="flex h-full items-stretch"
          >
            {/* Chunk sidebar list */}
            <Panel
              defaultSize={200}
              minSize={100}
              maxSize={300}
              className="flex flex-col bg-slate-50/40 border-r border-slate-200"
            >
              <div className="shrink-0 border-b border-slate-100 px-3 py-2 text-xs font-medium uppercase tracking-wide text-slate-400">
                Chunks
              </div>
              <div className="flex-1 overflow-y-auto">
                {map(chunkListState.items, (chunk, idx) => {
                  const isSelected = chunk.id === selectedChunkId;
                  const snippet = chunk.text
                    ? chunk.text.length > 50
                      ? `${chunk.text.slice(0, 50)}\u2026`
                      : chunk.text
                    : "No preview";

                  return (
                    <button
                      key={chunk.id}
                      type="button"
                      onClick={() =>
                        onChunkSelect(isSelected ? null : chunk.id)
                      }
                      className={`flex cursor-pointer w-full items-start gap-2 border-b border-slate-100 px-3 py-2.5 text-left transition-colors last:border-b-0 ${
                        isSelected
                          ? "bg-indigo-50/70 text-indigo-900"
                          : "text-slate-700 hover:bg-slate-100/60"
                      }`}
                    >
                      <span
                        className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded text-[10px] font-semibold ${
                          isSelected
                            ? "bg-indigo-600 text-white"
                            : "bg-slate-200 text-slate-600"
                        }`}
                      >
                        {idx + 1}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-xs leading-5">
                        {snippet}
                      </span>
                    </button>
                  );
                })}
              </div>
            </Panel>

            <PanelResizeHandle className="group relative z-10 flex w-[6px] shrink-0 cursor-col-resize flex-col items-center justify-center bg-slate-200 transition-colors hover:bg-violet-400 active:bg-violet-500">
              <div className="h-6 w-1 rounded-full bg-slate-400 group-hover:bg-white group-active:bg-white" />
            </PanelResizeHandle>

            {/* Detail panel */}
            <Panel minSize={30} className="flex flex-col bg-white">
              <div className="min-w-0 flex-1 flex flex-col">
                {!selectedChunkId ? (
                  <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 py-12 text-slate-400">
                    <Layers className="size-8 text-slate-300" />
                    <p className="text-sm">
                      Select a chunk from the list to view its metadata.
                    </p>
                  </div>
                ) : selectedChunkDetailState?.loading ? (
                  <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-500">
                    <Loader className="h-4 w-4 animate-spin text-violet-500" />
                    {config.loadingMessage}
                  </div>
                ) : selectedChunkDetailState?.error ? (
                  <div className="p-4">
                    <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
                      {selectedChunkDetailState.error}
                    </div>
                    <div className="mt-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => onRetryDetail(selectedChunkId)}
                        startIcon={<RefreshCw className="h-3.5 w-3.5" />}
                      >
                        Retry
                      </Button>
                    </div>
                  </div>
                ) : selectedChunkDetail ? (
                  <div className="flex flex-1 flex-col divide-y divide-slate-100 min-h-0">
                    {/* Text snippet */}
                    {selectedChunkSummary?.text ? (
                      <div className="flex flex-1 flex-col px-4 py-3 min-h-0">
                        <div className="shrink-0 pb-1.5 text-xs font-medium text-slate-500">
                          Text Snippet
                        </div>
                        <div className="flex-1 overflow-y-auto whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
                          <Markdown>{selectedChunkSummary.text}</Markdown>
                        </div>
                      </div>
                    ) : null}

                    <div className="shrink-0 overflow-y-auto border-t border-slate-100 max-h-[50%]">
                      {chunkMetadataEntries.length > 0 ? (
                        <div>
                          <div className="border-b border-slate-100 px-4 py-2 text-xs font-medium text-slate-500">
                            Metadata ({chunkMetadataEntries.length} fields)
                          </div>
                          <table className="min-w-full text-sm">
                            <tbody className="divide-y divide-slate-50">
                              {map(
                                chunkMetadataEntries,
                                ([key, value], idx) => (
                                  <tr
                                    key={key}
                                    className={
                                      idx % 2 === 0
                                        ? "bg-white"
                                        : "bg-slate-50/40"
                                    }
                                  >
                                    <td className="w-36 whitespace-nowrap px-4 py-2 align-top text-xs font-medium text-slate-500">
                                      {startCase(key)}
                                    </td>
                                    <td className="px-4 py-2 text-sm text-slate-800 wrap-break-word">
                                      {renderMetadataValue(value)}
                                    </td>
                                  </tr>
                                ),
                              )}
                            </tbody>
                          </table>
                        </div>
                      ) : (
                        <div className="px-4 py-6 text-center text-sm text-slate-500">
                          No metadata available for this chunk.
                        </div>
                      )}

                      {/* Raw JSON */}
                      <details className="group">
                        <summary className="flex cursor-pointer select-none items-center justify-between px-4 py-2 text-xs font-medium text-slate-500 hover:text-slate-700">
                          {config.rawJsonLabel}
                          <span className="transition-transform duration-200 group-open:rotate-180">
                            <ChevronDownIcon className="size-3.5" />
                          </span>
                        </summary>
                        <div className="border-t border-slate-100 px-4 py-3">
                          <JsonViewer
                            data={selectedChunkDetail.raw}
                            defaultExpanded={false}
                          />
                        </div>
                      </details>
                    </div>
                  </div>
                ) : (
                  <div className="flex h-full items-center justify-center p-6 text-sm text-slate-500">
                    Unable to locate chunk details.
                  </div>
                )}
              </div>
            </Panel>
          </PanelGroup>
        </div>
      )}
    </section>
  );
};
