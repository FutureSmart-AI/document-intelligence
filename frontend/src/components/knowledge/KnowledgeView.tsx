"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import Icon from "@/components/Icon";
import { Btn, EmptyNote, LinkBtn, PageHead, Select } from "@/components/ui";
import { useExtractedFields } from "@/data/extractionLog";
import { useKnowledgeMetadata } from "@/data/knowledgeTable";
import { useAutoRefresh } from "@/platform/components/rag/dashboard/knowledge/hooks/useAutoRefresh";
import type { BatchUploadError } from "@/platform/components/rag/dashboard/knowledge/hooks/useFileUpload.types";
import { DeleteKnowledgeDialog } from "@/platform/components/rag/dashboard/knowledge/list-view/DeleteKnowledgeDialog";
import { useKnowledgeDelete } from "@/platform/components/rag/dashboard/knowledge/list-view/useKnowledgeDelete";
import { useAuth } from "@/platform/hooks/useAuth";
import { useKnowledgeData } from "@/platform/hooks/useKnowledgeData";
import { cn } from "@/platform/utils";

import AddDocumentsDialog from "./AddDocumentsDialog";
import AddKnowledgeTiles, { type IngestSource } from "./AddKnowledgeTiles";
import DocumentDetail from "./detail/DocumentDetail";
import { buildTable } from "./table/buildTable";
import { PAGE_SIZE_OPTIONS, SHOW_CLICKUP_SOURCES } from "./table/constants";
import { exportTableCsv } from "./table/exportCsv";
import FacetBar from "./table/FacetBar";
import FiltersPanel from "./table/FiltersPanel";
import KnowledgeGrid from "./table/KnowledgeGrid";
import KnowledgeToolbar from "./table/KnowledgeToolbar";
import { selectRows, statusCounts } from "./table/selectRows";
import SortPanel from "./table/SortPanel";
import { useTableState, visibleColumns } from "./table/useTableState";

/**
 * Knowledge — every document in this collection, its status, and every field
 * pulled out of it, in one table.
 *
 * This one screen is what used to be three. The document list said what was in
 * the collection and whether it had been read; the Data table showed the
 * extracted values; Explore counted how often each value occurred. They were
 * three views of the same rows, which meant three toolbars, three sorts and
 * three ideas of what a document's type was — and answering "which resumes
 * mention Kubernetes and did they all parse" meant holding two screens in your
 * head. Here the document facts and the extracted fields are column groups in
 * one grid, and the value counts are a band above it that writes real filters.
 */
export default function KnowledgeView() {
  const { knowledgeId, isKnowledgeEnabled } = useAuth();
  const [source, setSource] = useState<IngestSource | null>(null);
  const [uploadErrors, setUploadErrors] = useState<BatchUploadError[]>([]);
  const [showAddOptions, setShowAddOptions] = useState(true);
  const [openPanel, setOpenPanel] = useState<"filters" | "sort" | null>(null);
  const [previewObjectId, setPreviewObjectId] = useState<string | null>(null);
  const [isPreviewExpanded, setIsPreviewExpanded] = useState(false);

  const { data, isLoading, isFetching, refetch } = useKnowledgeData();
  const {
    metadata,
    isLoading: isMetadataLoading,
    isFetching: isMetadataFetching,
    error: metadataError,
    refetch: refetchMetadata,
  } = useKnowledgeMetadata();
  const { extractedFields, refetch: refetchExtractedFields } =
    useExtractedFields();

  const state = useTableState();

  const table = useMemo(
    () =>
      buildTable({
        documents: data?.allData,
        metadataColumns: metadata?.columns,
        metadataRows: metadata?.rows,
        extractionContext: metadata?.extractionContext,
        extractedFields,
      }),
    [
      data?.allData,
      metadata?.columns,
      metadata?.rows,
      metadata?.extractionContext,
      extractedFields,
    ],
  );

  const visibleCols = useMemo(
    () => visibleColumns(table.columns, state.groups, state.hidden),
    [table.columns, state.groups, state.hidden],
  );

  const viewRows = useMemo(
    () => selectRows(table.rows, table.columns, state.query),
    [table.rows, table.columns, state.query],
  );

  const counts = useMemo(() => statusCounts(table.rows), [table.rows]);

  const pageCount = Math.max(
    1,
    Math.ceil(viewRows.length / state.pageSize) || 1,
  );
  // A filter that empties the last page must not leave the reader on it.
  const page = Math.min(state.page, pageCount);
  const start = (page - 1) * state.pageSize;
  const pageRows = viewRows.slice(start, start + state.pageSize);

  const stableRefetch = useCallback(() => {
    refetch();
    refetchMetadata();
    // A document that just finished was logged with the schema it ran under;
    // without this its new fields would read as never extracted until reload.
    refetchExtractedFields();
  }, [refetch, refetchMetadata, refetchExtractedFields]);

  // Documents that are still being read update themselves, so the table does
  // not have to be refreshed by hand to watch one finish.
  useAutoRefresh({
    allData: data?.allData,
    refetch: stableRefetch,
    enabled: Boolean(knowledgeId) && !isLoading,
  });

  const {
    deleteModalOpen,
    deleting,
    deleteRecord,
    handleDelete,
    confirmDelete,
    closeModal: closeDeleteModal,
  } = useKnowledgeDelete({ onSuccess: stableRefetch });

  const closePreview = useCallback(() => {
    setPreviewObjectId(null);
    setIsPreviewExpanded(false);
  }, []);

  const previewIndex = useMemo(
    () =>
      previewObjectId
        ? viewRows.findIndex((row) => row.objectId === previewObjectId)
        : -1,
    [viewRows, previewObjectId],
  );

  const previewRow = useMemo(
    () =>
      previewObjectId
        ? (table.rows.find((row) => row.objectId === previewObjectId) ?? null)
        : null,
    [table.rows, previewObjectId],
  );

  const goToPreviewOffset = useCallback(
    (offset: number) => {
      if (previewIndex < 0) return;
      const next = viewRows[previewIndex + offset];
      if (next) setPreviewObjectId(next.objectId);
    },
    [viewRows, previewIndex],
  );

  // Escape is the escape hatch from full screen; the note above the table says
  // so in words too, rather than leaving it to the key alone.
  const { fullScreen, setFullScreen } = state;
  useEffect(() => {
    if (!fullScreen) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFullScreen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [fullScreen, setFullScreen]);

  const emptyNote = table.rows.length
    ? "No document matches what you have selected. Clear a filter, or change the search above."
    : "No documents yet. Add some above — once they have been read, each one becomes a row here.";

  return (
    <>
      {!fullScreen && (
        <PageHead
          tight
          title="Knowledge"
          sub="Every document in this collection, its status, and every field pulled out of it — one table, one place."
          action={
            <Btn
              size="sm"
              data-demo="knowledge-add-toggle"
              onClick={() => setShowAddOptions((value) => !value)}
              aria-expanded={showAddOptions}
              title="Show or hide the ways to add a document"
              startIcon={<Icon name="plus" className="size-3.5" />}
            >
              {showAddOptions ? "Hide add options" : "Add documents"}
            </Btn>
          }
        />
      )}

      {!isKnowledgeEnabled && (
        <EmptyNote className="mb-4">
          This collection&rsquo;s documents are paused. Re-enable the knowledge
          service from the main dashboard to add or read documents.
        </EmptyNote>
      )}

      {!fullScreen && showAddOptions && (
        <AddKnowledgeTiles
          compact
          onPick={setSource}
          disabled={{ clickup: !SHOW_CLICKUP_SOURCES }}
        />
      )}

      {metadataError && (
        <EmptyNote className="mb-3">
          The extracted fields could not be read, so the table shows the
          documents and their status only.{" "}
          <LinkBtn onClick={() => refetchMetadata()}>Try again</LinkBtn>
        </EmptyNote>
      )}

      {metadata?.truncated && (
        <p className="mb-3 text-[12px] text-ink-2">
          This collection is larger than the table can read in one go, so the
          extracted fields are filled in for{" "}
          {metadata.documentCount.toLocaleString()} of its documents. The rest
          are listed with their status, and their columns read as empty until
          the set is narrowed with a filter.
        </p>
      )}

      {state.facetOpen && (
        <FacetBar
          rows={table.rows}
          viewRows={viewRows}
          columns={table.columns}
          visibleCols={visibleCols}
          query={state.query}
          // Opens on the first extracted field, which is what someone came to
          // group by; the document columns are the fallback for a collection
          // whose extraction has not produced any fields yet.
          field={state.facetField ?? table.fieldColumns[0]?.key ?? "addedvia"}
          onFieldChange={state.setFacetField}
          viz={state.viz}
          onVizChange={state.setViz}
          onToggleValue={state.toggleFacetValue}
          onClearFilters={() => state.updateFilters([])}
        />
      )}

      <KnowledgeToolbar
        search={state.search}
        onSearchChange={state.updateSearch}
        counts={counts}
        status={state.status}
        onStatusChange={state.updateStatus}
        type={state.type}
        onTypeChange={state.updateType}
        groups={state.groups}
        onToggleGroup={state.toggleGroup}
        onGroupPreset={state.applyGroupPreset}
        columns={table.columns}
        hidden={state.hidden}
        visibleCount={visibleCols.length}
        onSetHidden={state.setColumnHidden}
        onSetMany={state.setManyHidden}
        filterCount={state.filters.length}
        filtersOpen={openPanel === "filters"}
        onToggleFilters={() =>
          setOpenPanel((current) => (current === "filters" ? null : "filters"))
        }
        sortCount={state.sorts.length}
        sortOpen={openPanel === "sort"}
        onToggleSort={() =>
          setOpenPanel((current) => (current === "sort" ? null : "sort"))
        }
        facetOpen={state.facetOpen}
        onToggleFacet={() => state.setFacetOpen(!state.facetOpen)}
        compact={state.compact}
        onToggleCompact={() => state.setCompact(!state.compact)}
        fullScreen={fullScreen}
        onToggleFullScreen={() => setFullScreen(!fullScreen)}
        isFetching={isFetching || isMetadataFetching}
        onRefresh={stableRefetch}
        onExportVisible={() =>
          exportTableCsv({
            rows: viewRows,
            columns: visibleCols,
            prefix: "documents-in-view",
          })
        }
        onExportAll={() =>
          exportTableCsv({
            rows: table.rows,
            columns: table.columns,
            prefix: "documents-all",
          })
        }
      />

      {openPanel === "filters" && (
        <FiltersPanel
          columns={table.columns}
          filters={state.filters}
          onChange={state.updateFilters}
          onClear={() => state.updateFilters([])}
          onAdd={() =>
            state.addFilterRule(
              table.fieldColumns[0]?.key ?? table.columns[0]?.key ?? "file",
            )
          }
          onDone={() => setOpenPanel(null)}
        />
      )}

      {openPanel === "sort" && (
        <SortPanel
          columns={table.columns}
          sorts={state.sorts}
          onChange={state.updateSorts}
          onAdd={() =>
            state.updateSorts([
              ...state.sorts,
              { field: table.columns[0]?.key ?? "file", dir: 1 },
            ])
          }
          onDone={() => setOpenPanel(null)}
        />
      )}

      {/* `di-analytics` remaps the slate/white variables the agent UI's own
          components are painted in, so the reused detail panel *and*
          `SidePreview`'s own chrome follow this product's theme. It has to wrap
          the panel's container, not just its contents — with the class inside,
          the panel went dark while the frame around it stayed white. Nothing in
          this product's own kit is affected: those are drawn in `--di-*`. */}
      <div className="di-analytics relative flex min-h-0 gap-0">
        <div className="flex min-w-0 flex-1 flex-col">
          {/* One card: the scrolling table and the footer that counts it are
              the same object, so the count sits on the table's own bottom edge
              rather than floating under it as a second panel. */}
          <div className="overflow-hidden rounded-di-lg border border-line bg-surface">
            {isLoading || isMetadataLoading ? (
              <div className="px-4 py-12 text-center text-[13px] text-ink-2">
                Reading this collection…
              </div>
            ) : (
              <KnowledgeGrid
                columns={visibleCols}
                rows={pageRows}
                fieldCount={table.fieldColumns.length}
                sorts={state.sorts}
                compact={state.compact || fullScreen}
                fullScreen={fullScreen}
                activeObjectId={previewObjectId}
                emptyNote={emptyNote}
                onHeaderSort={state.toggleHeaderSort}
                onToggleGroup={state.toggleGroup}
                onOpenRow={(row) => setPreviewObjectId(row.objectId)}
                onDelete={handleDelete}
              />
            )}

            <div
              data-demo="knowledge-footer"
              className="flex flex-wrap items-center gap-2.5 border-t border-line px-3.5 py-2.5 text-[12.5px] text-ink-2"
            >
              <span className="tnum">
                {viewRows.length
                  ? `Showing ${start + 1}–${start + pageRows.length} of ${viewRows.length} ${
                      viewRows.length === 1 ? "document" : "documents"
                    }`
                  : `Showing 0 of ${table.rows.length} documents`}
              </span>
              <span className="flex-1" />
              <span className="hidden text-[11.5px] text-ink-3 sm:inline">
                Click any row for everything in that document
              </span>
              <label className="flex items-center gap-1.5">
                Rows:
                <Select
                  fit
                  aria-label="Rows per page"
                  value={state.pageSize}
                  onChange={(event) =>
                    state.setPageSize(Number(event.target.value))
                  }
                  className="py-1 text-[12px]"
                >
                  {PAGE_SIZE_OPTIONS.map((size) => (
                    <option key={size} value={size}>
                      {size}
                    </option>
                  ))}
                </Select>
              </label>
              <div className="flex items-center gap-1">
                <PageButton
                  label="Previous page"
                  disabled={page <= 1}
                  onClick={() => state.setPage(page - 1)}
                >
                  ‹
                </PageButton>
                <span className="tnum rounded-di-sm border border-accent bg-accent-soft px-2 py-1 text-[12px] font-semibold text-accent">
                  {page}
                </span>
                <PageButton
                  label="Next page"
                  disabled={page >= pageCount}
                  onClick={() => state.setPage(page + 1)}
                >
                  ›
                </PageButton>
              </div>
            </div>
          </div>

          {uploadErrors.length > 0 && (
            <EmptyNote className="mt-3">
              {uploadErrors.length}{" "}
              {uploadErrors.length === 1 ? "file" : "files"} could not be
              uploaded:{" "}
              {uploadErrors
                .map((error) => `${error.file_name} (${error.error_detail})`)
                .join(", ")}
              <span className="ml-2 inline-block">
                <LinkBtn onClick={() => setUploadErrors([])}>Dismiss</LinkBtn>
              </span>
            </EmptyNote>
          )}
        </div>
      </div>

      {/* The document, full width and two columns — the text beside the values
          pulled out of it. Rendered outside the table's own row so it overlays
          the screen rather than squeezing the grid into a third of it. */}
      {previewRow && (
        <DocumentDetail
          row={previewRow}
          fieldColumns={table.fieldColumns}
          positionLabel={
            previewIndex >= 0
              ? `${previewIndex + 1} of ${viewRows.length}`
              : undefined
          }
          hasPrev={previewIndex > 0}
          hasNext={previewIndex >= 0 && previewIndex < viewRows.length - 1}
          expanded={isPreviewExpanded}
          onPrev={() => goToPreviewOffset(-1)}
          onNext={() => goToPreviewOffset(1)}
          onToggleExpanded={() => setIsPreviewExpanded((value) => !value)}
          onDelete={() => handleDelete(previewRow.document)}
          onClose={closePreview}
        />
      )}

      <DeleteKnowledgeDialog
        open={deleteModalOpen}
        record={deleteRecord}
        deleting={deleting}
        onClose={closeDeleteModal}
        onConfirm={confirmDelete}
      />

      {source && knowledgeId && (
        <AddDocumentsDialog
          // Keyed on the source so each tile opens a fresh dialog with its own
          // form rather than reusing the last one's state.
          key={source}
          knowledgeId={knowledgeId}
          source={source}
          onClose={() => setSource(null)}
          onDone={stableRefetch}
          onBatchErrors={(errors) => {
            setUploadErrors(errors);
            setSource(null);
          }}
        />
      )}
    </>
  );
}

function PageButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "inline-flex size-7 items-center justify-center rounded-di-sm border border-line bg-surface text-ink-2",
        disabled
          ? "cursor-not-allowed opacity-40"
          : "cursor-pointer hover:bg-sunken hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}
