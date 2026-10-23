"use client";

import { useCallback, useMemo, useState } from "react";

import Icon from "@/components/Icon";
import { Btn, EmptyNote, PageHead } from "@/components/ui";
import { useCollection } from "@/data/collections";
import {
  DataTable,
  DataViewDetailPanel,
  DataViewToolbar,
  useDataViewExport,
} from "@/platform/components/rag/dashboard/data-view";
import { visibleColumnNames } from "@/platform/components/rag/dashboard/data-view/columnVisibility";
import { SidePreview, TablePagination } from "@/platform/components/ui";
import { useAuth } from "@/platform/hooks/useAuth";
import { useDataView } from "@/platform/hooks/useDataView";

/**
 * Structured data — one row per document, in the schema the collection defines.
 *
 * Columns, filters, multi-rule sort, the CSV export and the document preview are
 * the agent UI's own Data view: same request shape, same 100-row page cap on the
 * metadata route, same export that walks past it. What the product adds is the
 * framing and the wording — "documents", not "metadata points".
 */
export default function DataView() {
  const { agentId, knowledgeId } = useAuth();
  // The knowledge base arrives with the collection, so until that request
  // settles "no documents" is not yet a fact — saying it anyway made a loading
  // page look like an empty one.
  const { isLoading: isCollectionLoading } = useCollection(
    agentId ?? undefined,
  );
  const [previewObjectId, setPreviewObjectId] = useState<string | null>(null);
  const [isPreviewExpanded, setIsPreviewExpanded] = useState(false);

  const {
    data,
    isLoading,
    isFetching,
    error,
    refetch,
    updatePageSize,
    updateFilters,
    updateSort,
    updateColumns,
    goToPage,
    request,
  } = useDataView();

  const {
    exportCsv,
    isExporting,
    progress: exportProgress,
  } = useDataViewExport();

  /** Column names the table is actually rendering — the CSV mirrors them. */
  const visibleColumns = useMemo(
    () => visibleColumnNames(data?.columns ?? []),
    [data?.columns],
  );

  const handleExportCsv = useCallback(() => {
    // Pagination is deliberately dropped: the file covers every row matching the
    // current filters and sort, not just the page on screen.
    void exportCsv(
      {
        columns: request.columns,
        filters: request.filters,
        sort: request.sort,
      },
      visibleColumns,
    );
  }, [
    exportCsv,
    request.columns,
    request.filters,
    request.sort,
    visibleColumns,
  ]);

  const rows = useMemo(() => data?.rows ?? [], [data?.rows]);

  // Prev/next walk the page currently on screen, matching what the user sees.
  const previewIndex = useMemo(
    () =>
      previewObjectId
        ? rows.findIndex((row) => row.object_id === previewObjectId)
        : -1,
    [rows, previewObjectId],
  );

  const closePreview = useCallback(() => {
    setPreviewObjectId(null);
    setIsPreviewExpanded(false);
  }, []);

  const goToPreviewOffset = useCallback(
    (offset: number) => {
      if (previewIndex < 0) return;
      const next = rows[previewIndex + offset];
      if (next?.object_id) setPreviewObjectId(next.object_id);
    },
    [rows, previewIndex],
  );

  if (!knowledgeId) {
    return isCollectionLoading ? (
      <p className="text-[13px] text-ink-2">Opening this collection…</p>
    ) : (
      <EmptyNote>
        This collection has no documents attached yet — add some from Knowledge.
      </EmptyNote>
    );
  }

  if (error) {
    return (
      <EmptyNote>
        <p className="mb-3 text-danger-text">
          {(error as { message?: string })?.message ??
            "This collection's table could not be read."}
        </p>
        <Btn onClick={() => refetch()} disabled={isFetching}>
          Retry
        </Btn>
      </EmptyNote>
    );
  }

  return (
    <>
      <PageHead
        title="Structured data"
        sub="Every document as one row, in the schema you defined. Click any column header to sort by it."
        action={
          <div className="flex items-center gap-3">
            {data && (
              <span className="tnum text-[12.5px] text-ink-2">
                {data.total_count.toLocaleString()}{" "}
                {data.total_count === 1 ? "document" : "documents"}
              </span>
            )}
            <Btn
              size="sm"
              onClick={() => refetch()}
              disabled={isFetching}
              startIcon={
                <Icon
                  name="ref"
                  className={`size-3.5 ${isFetching ? "animate-spin" : ""}`}
                />
              }
            >
              Refresh
            </Btn>
          </div>
        }
      />

      <div className="relative flex min-h-0 gap-0 overflow-hidden">
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <DataViewToolbar
            columns={data?.columns || []}
            selectedColumns={request.columns || []}
            filters={request.filters || {}}
            sort={request.sort || []}
            onColumnsChange={updateColumns}
            onFiltersChange={updateFilters}
            onSortChange={updateSort}
            onClearColumns={() => updateColumns([])}
            onClearFilters={() => updateFilters({})}
            onClearSort={() => updateSort([])}
            onExportCsv={handleExportCsv}
            isExporting={isExporting}
            exportProgress={exportProgress}
            exportDisabled={isLoading || (data?.total_count ?? 0) === 0}
          />

          <DataTable
            columns={data?.columns || []}
            rows={rows}
            isLoading={isLoading}
            isFetching={isFetching}
            onRowClick={(row: Record<string, unknown>) => {
              const objectId = row.object_id as string | undefined;
              if (objectId) setPreviewObjectId(objectId);
            }}
            isRowActive={(row: Record<string, unknown>) =>
              !!previewObjectId && row.object_id === previewObjectId
            }
            page={request.page || 1}
            pageSize={request.page_size || 10}
            sorting={(request.sort || []).map((entry) => {
              const column = data?.columns.find(
                (candidate) => candidate.sort_field === entry.field,
              );
              return {
                id: column ? column.name : entry.field,
                desc: entry.order === "desc",
              };
            })}
            onSortingChange={(updater) => {
              const current = (request.sort || []).map((entry) => {
                const column = data?.columns.find(
                  (candidate) => candidate.sort_field === entry.field,
                );
                return {
                  id: column ? column.name : entry.field,
                  desc: entry.order === "desc",
                };
              });
              const next =
                typeof updater === "function" ? updater(current) : updater;
              updateSort(
                next
                  .map((entry) => {
                    const column = data?.columns.find(
                      (candidate) => candidate.name === entry.id,
                    );
                    return {
                      field: (column?.sort_field || entry.id) as string,
                      order: (entry.desc ? "desc" : "asc") as "desc" | "asc",
                    };
                  })
                  .filter((entry) => Boolean(entry.field)),
              );
            }}
          />

          {data && data.rows.length > 0 && (
            <TablePagination
              pageIndex={(request.page || 1) - 1}
              pageSize={request.page_size || 10}
              totalItems={data.total_count}
              disabled={isFetching}
              onPageChange={({ pageIndex, pageSize }) => {
                // A size change resets to page 1 in the hook, so only one of the
                // two ever needs sending.
                if (pageSize !== (request.page_size || 10)) {
                  updatePageSize(pageSize);
                  return;
                }
                goToPage(pageIndex + 1);
              }}
              className="shrink-0 rounded-di-lg border border-line bg-surface"
            />
          )}

          {!isLoading && data && data.rows.length === 0 && (
            <EmptyNote>
              No documents to show yet. Add some from Knowledge — once they have
              been read, each one becomes a row here.
            </EmptyNote>
          )}
        </div>

        <SidePreview
          open={!!previewObjectId}
          expanded={isPreviewExpanded}
          onClose={closePreview}
          onCollapse={() => setIsPreviewExpanded(false)}
        >
          <DataViewDetailPanel
            objectId={previewObjectId}
            variant="drawer"
            onClose={closePreview}
            onPrev={() => goToPreviewOffset(-1)}
            onNext={() => goToPreviewOffset(1)}
            hasPrev={previewIndex > 0}
            hasNext={previewIndex >= 0 && previewIndex < rows.length - 1}
            positionLabel={
              previewIndex >= 0
                ? `${previewIndex + 1} of ${rows.length}`
                : undefined
            }
            expanded={isPreviewExpanded}
            onToggleExpanded={() => setIsPreviewExpanded((value) => !value)}
          />
        </SidePreview>
      </div>
    </>
  );
}
