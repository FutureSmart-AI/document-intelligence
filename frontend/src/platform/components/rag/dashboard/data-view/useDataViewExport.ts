"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { isAxiosError } from "axios";

import { useToast } from "@/platform/components/ToastContext";
import { useAuth } from "@/platform/hooks/useAuth";
import type { DataViewRequest } from "@/platform/types/data-view";
import { csvFilename, downloadRowsAsCsv } from "@/platform/utils/csv";

import {
  backfillDocumentColumns,
  enrichRowsWithFullSchema,
  ExportTooLargeError,
  fetchAllDataViewRows,
  toCsvColumns,
} from "./exportDataView";

export interface ExportProgress {
  fetched: number;
  total: number;
}

/** "Source, File Name and 3 more" — enough to recognise, short enough for a toast. */
const formatColumnList = (names: string[], limit = 3): string => {
  const shown = names
    .slice(0, limit)
    .map((name) =>
      name
        .split("_")
        .map((word) =>
          word ? word.charAt(0).toUpperCase() + word.slice(1) : word,
        )
        .join(" "),
    )
    .join(", ");

  const rest = names.length - limit;
  return rest > 0 ? `${shown} and ${rest} more` : shown;
};

/**
 * Exports the full filtered Data View result set as CSV. The table only holds
 * one page, so this re-runs the same request without pagination before
 * serializing (see `fetchAllDataViewRows` for why paging is not an option).
 */
export const useDataViewExport = () => {
  const { knowledgeId } = useAuth();
  const { toast } = useToast();
  const [isExporting, setIsExporting] = useState(false);
  const [progress, setProgress] = useState<ExportProgress | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  const exportCsv = useCallback(
    async (request: DataViewRequest, visibleColumns?: string[]) => {
      if (!knowledgeId || isExporting) return;

      const controller = new AbortController();
      abortRef.current = controller;
      setIsExporting(true);
      setProgress({ fetched: 0, total: 0 });

      try {
        const { rows, columns, totalCount } = await fetchAllDataViewRows({
          knowledgeId,
          request,
          signal: controller.signal,
          onProgress: (fetched, total) => setProgress({ fetched, total }),
        });

        if (controller.signal.aborted) return;

        if (rows.length === 0) {
          toast.info("Nothing to export — no rows match the current filters.");
          return;
        }

        // The v1 walk reads every row but only the narrow schema. Re-read those
        // same documents through v2, batched by `object_id`, to get the columns
        // the table shows (`source`, `last_modified`, `clickup_*`, …).
        const wide = await enrichRowsWithFullSchema({
          knowledgeId,
          rows,
          columns,
          request,
          signal: controller.signal,
          onProgress: (done, total) => setProgress({ fetched: done, total }),
        });

        if (controller.signal.aborted) return;

        // Anything neither route carries is joined on from the document list.
        const filled = await backfillDocumentColumns({
          knowledgeId,
          rows: wide.rows,
          columns: wide.columns,
          visibleColumns,
        });

        if (controller.signal.aborted) return;

        const csvColumns = toCsvColumns(filled.columns, visibleColumns);
        downloadRowsAsCsv(filled.rows, csvFilename("data-view"), csvColumns);

        // Whatever neither route could supply is named outright rather than
        // left for the user to notice in Excel.
        const written = new Set(csvColumns.map((col) => col.key));
        const droppedColumns = (visibleColumns ?? []).filter(
          (name) => !written.has(name),
        );

        // Row count comes from the file, never from the spine walk: the v2
        // re-read can return points the v1 cursor missed, so the CSV can be
        // longer than the walk was.
        const exportedRows = filled.rows.length;
        const partial = exportedRows < totalCount;
        const exported = exportedRows.toLocaleString();
        const warnings: string[] = [];

        if (partial) {
          // Never claim a full export we did not get: the row count in the file
          // is the truth, the document total is what the header shows.
          warnings.push(
            `Exported ${exported} of ${totalCount.toLocaleString()} rows — the API stopped returning rows before the end of the result set. Narrow the filters and export in parts.`,
          );
        }
        if (droppedColumns.length) {
          warnings.push(
            `${droppedColumns.length} ${droppedColumns.length === 1 ? "column is" : "columns are"} missing from the file — the export API does not return ${formatColumnList(droppedColumns)}.`,
          );
        }

        if (warnings.length) {
          toast.warning(
            partial
              ? warnings.join(" ")
              : `Exported ${exported} ${exportedRows === 1 ? "row" : "rows"}. ${warnings.join(" ")}`,
          );
        } else {
          toast.success(
            `Exported ${exported} ${exportedRows === 1 ? "row" : "rows"} to CSV.`,
          );
        }
      } catch (error) {
        // Row-limit rejections are ours to explain; transport errors are
        // already surfaced by the global axios toast interceptor. Anything else
        // still has to say something — a click that does nothing is a bug
        // report we cannot act on.
        if (error instanceof ExportTooLargeError) {
          toast.error(error.message);
        } else if (!isAxiosError(error)) {
          console.error("Data View CSV export failed", error);
          toast.error(
            `Export failed: ${error instanceof Error ? error.message : "unexpected error"}`,
          );
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setIsExporting(false);
        setProgress(null);
      }
    },
    [knowledgeId, isExporting, toast],
  );

  return { exportCsv, isExporting, progress };
};
