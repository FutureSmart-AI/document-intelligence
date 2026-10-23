"use client";

import { useEffect, useState } from "react";

import { Download, ExternalLink, Loader2 } from "lucide-react";

import Button from "@/platform/components/ui/Button";
import { Modal } from "@/platform/components/ui/Modal";

import { fetchDatasetRows } from "./fetchDatasetRows";
import PreviewTable from "./PreviewTable";
import { downloadCsv } from "./rowsToCsv";
import { isSafeHttpUrl } from "./urlSafety";

interface FullDatasetModalProps {
  open: boolean;
  resultS3Url?: string;
  /** Rows to show when no full-dataset URL is available (preview fallback). */
  fallbackRows?: Record<string, unknown>[];
  onClose: () => void;
}

type State =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | {
      status: "ready";
      rows: Record<string, unknown>[];
      isFallback: boolean;
      corsBlocked?: boolean;
    };

export const FullDatasetModal = ({
  open,
  resultS3Url,
  fallbackRows,
  onClose,
}: FullDatasetModalProps) => {
  const [state, setState] = useState<State>({ status: "idle" });

  useEffect(() => {
    if (!open) {
      setState({ status: "idle" });
      return () => {};
    }

    // No safe full-dataset URL: show the preview rows we already have rather
    // than failing with "Unsafe or missing dataset URL".
    if (!isSafeHttpUrl(resultS3Url)) {
      if (fallbackRows && fallbackRows.length > 0) {
        setState({ status: "ready", rows: fallbackRows, isFallback: true });
      } else {
        setState({
          status: "error",
          message: "No dataset is available for this result.",
        });
      }
      return () => {};
    }

    let cancelled = false;
    const controller = new AbortController();
    setState({ status: "loading" });
    fetchDatasetRows(resultS3Url, controller.signal)
      .then((rows) => {
        if (!cancelled) setState({ status: "ready", rows, isFallback: false });
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
        if (cancelled) return;
        // TypeError("Failed to fetch") = CORS block or network error.
        // Fall back to preview rows so the modal stays useful.
        const isNetworkError =
          err instanceof TypeError ||
          (err instanceof Error && err.message === "Failed to fetch");
        if (isNetworkError && fallbackRows && fallbackRows.length > 0) {
          setState({
            status: "ready",
            rows: fallbackRows,
            isFallback: true,
            corsBlocked: true,
          });
        } else {
          setState({
            status: "error",
            message:
              err instanceof Error ? err.message : "Failed to load dataset",
          });
        }
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [open, resultS3Url, fallbackRows]);

  const rows = state.status === "ready" ? state.rows : [];
  const isFallback = state.status === "ready" && state.isFallback;
  const corsBlocked = state.status === "ready" && state.corsBlocked;
  const hasDirectDownload = corsBlocked && isSafeHttpUrl(resultS3Url);

  return (
    <Modal
      open={open}
      title="Query Results: Full Dataset"
      showFooter={false}
      onCancel={onClose}
      fullscreen
    >
      <div className="flex flex-col gap-3 flex-1 min-h-0">
        <div className="shrink-0 flex items-center justify-between gap-2">
          <p className="text-xs text-slate-500">
            {state.status === "ready"
              ? corsBlocked
                ? `Showing ${rows.length} preview rows • Download for full dataset`
                : isFallback
                  ? `Showing ${rows.length} preview rows • Full export not available`
                  : `Showing ${rows.length} rows • Loaded securely from S3`
              : "Loading dataset"}
          </p>
          <div className="flex items-center gap-2">
            {hasDirectDownload && (
              <a
                href={resultS3Url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 shadow-sm hover:bg-slate-50"
              >
                <ExternalLink className="w-3.5 h-3.5" />
                Download Full CSV
              </a>
            )}
            <Button
              variant="outline"
              size="sm"
              disabled={state.status !== "ready" || rows.length === 0}
              startIcon={<Download className="w-3.5 h-3.5" />}
              onClick={() =>
                downloadCsv(
                  rows,
                  isFallback ? "nl2sql-preview.csv" : "nl2sql-full.csv",
                )
              }
            >
              {isFallback ? "Export Preview CSV" : "Download Full CSV"}
            </Button>
          </div>
        </div>

        {state.status === "loading" && (
          <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading dataset…
          </div>
        )}
        {state.status === "error" && (
          <div className="py-10 text-center text-sm text-red-600">
            {state.message}
          </div>
        )}
        {state.status === "ready" && (
          <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
            <PreviewTable
              rows={rows}
              pageSize={15}
              emptyMessage="Dataset is empty"
            />
          </div>
        )}
      </div>
    </Modal>
  );
};

export default FullDatasetModal;
