"use client";

import { useCallback, useState } from "react";

import { Download, Maximize2 } from "lucide-react";

import Button from "@/platform/components/ui/Button";

import { fetchDatasetRows } from "./fetchDatasetRows";
import { downloadCsv } from "./rowsToCsv";
import { isSafeHttpUrl } from "./urlSafety";

interface ResultActionsProps {
  previewRows?: Record<string, unknown>[];
  /** Full result set staged by the backend (S3). Present for most queries. */
  resultS3Url?: string;
  onViewFullDataset: () => void;
}

/**
 * Export CSV downloads the *full* result set when the backend staged one —
 * exporting only the preview page (10 rows) was a QA bug. The
 * preview is used solely as a fallback, and the button says so.
 */
export const ResultActions = ({
  previewRows = [],
  resultS3Url,
  onViewFullDataset,
}: ResultActionsProps) => {
  const [isExporting, setIsExporting] = useState(false);
  const [fullExportFailed, setFullExportFailed] = useState(false);
  const hasFullDataset = isSafeHttpUrl(resultS3Url) && !fullExportFailed;

  const handleExport = useCallback(async () => {
    if (!hasFullDataset) {
      downloadCsv(previewRows, "nl2sql-preview.csv");
      return;
    }

    setIsExporting(true);
    try {
      const rows = await fetchDatasetRows(resultS3Url);
      downloadCsv(rows, "nl2sql-full.csv");
    } catch {
      // S3 unreachable (CORS/network): fall back to the preview rows and
      // relabel the button so the user knows the export is partial.
      setFullExportFailed(true);
      if (previewRows.length > 0) {
        downloadCsv(previewRows, "nl2sql-preview.csv");
      }
    } finally {
      setIsExporting(false);
    }
  }, [hasFullDataset, previewRows, resultS3Url]);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        disabled={!hasFullDataset && previewRows.length === 0}
        loading={isExporting}
        startIcon={<Download className="w-3.5 h-3.5" />}
        onClick={handleExport}
        title={
          hasFullDataset
            ? "Download every row of this result as CSV"
            : "Only the preview rows are available for this result"
        }
      >
        {hasFullDataset ? "Export CSV" : "Export Preview CSV"}
      </Button>
      <Button
        variant="primary"
        size="sm"
        startIcon={<Maximize2 className="w-3.5 h-3.5" />}
        onClick={onViewFullDataset}
        title="View full dataset"
      >
        View Full Dataset
      </Button>
    </div>
  );
};

export default ResultActions;
