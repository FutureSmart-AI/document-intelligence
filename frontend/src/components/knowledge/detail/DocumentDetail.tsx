"use client";

import { useEffect, useState } from "react";

import { FAILED } from "@/components/conversations/constants";
import Icon from "@/components/Icon";
import type {
  DocumentRow,
  TableColumn,
} from "@/components/knowledge/table/types";
import { Btn, IconBtn, Pill } from "@/components/ui";
import {
  useDocumentSections,
  useEnrichmentFields,
} from "@/data/documentDetail";
import { copyText } from "@/lib/clipboard";
import {
  parsedFileName,
  resolveDocumentLinks,
} from "@/platform/components/rag/dashboard/knowledge/list-view/documentLinks";
import { useToast } from "@/platform/components/ToastContext";
import { downloadExternalFile } from "@/platform/utils/downloadHelper";

import DocumentFields from "./DocumentFields";
import DocumentViewer from "./DocumentViewer";
import { documentKind } from "./kind";
import type { TextMatch } from "./locate";
import ProcessingTelemetry from "./ProcessingTelemetry";
import { readProcessingTelemetry } from "./readProcessing";
import { rowJson } from "./rowJson";

/**
 * One document: what it says, beside what was pulled out of it.
 *
 * Two columns, the width of the screen, because that is the job — the reason to
 * open a document from the table is to check a value against the text it came
 * from, and a 560px drawer can hold one of those two things at a time. The
 * agent UI's own drawer is still what the rest of that application uses; this
 * product reads its documents differently enough to draw them itself.
 *
 * It is an overlay rather than a route so the table underneath keeps its
 * filters, its sort and its page: stepping through eight documents with ‹ › and
 * closing must land you exactly where you were, and a route would have to
 * rebuild all of that from the URL to manage it.
 */
export default function DocumentDetail({
  row,
  fieldColumns,
  positionLabel,
  hasPrev,
  hasNext,
  expanded,
  onPrev,
  onNext,
  onToggleExpanded,
  onDelete,
  onClose,
}: {
  row: DocumentRow;
  fieldColumns: TableColumn[];
  /** "3 of 14" — where this document sits in the list it was opened from. */
  positionLabel?: string;
  hasPrev: boolean;
  hasNext: boolean;
  expanded: boolean;
  onPrev: () => void;
  onNext: () => void;
  onToggleExpanded: () => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const { toast } = useToast();
  const { data: sections = [], isLoading } = useDocumentSections(row.objectId);
  const { data: schema = [] } = useEnrichmentFields();
  /**
   * The value last asked "where is this in the text". Kept with the document it
   * was asked on, so stepping to the next document drops it without an effect.
   */
  const [located, setLocated] = useState<
    (TextMatch & { objectId: string; label: string }) | null
  >(null);
  const highlight = located?.objectId === row.objectId ? located : null;

  // Escape closes, and the arrows step — the pager is the reason this is worth
  // opening from a table rather than from a link, so it has to work without
  // reaching for the mouse.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "ArrowLeft" && hasPrev) onPrev();
      if (event.key === "ArrowRight" && hasNext) onNext();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, onPrev, onNext, hasPrev, hasNext]);

  const item = row.document;
  const fileName = String(row.values.file ?? item.file_name ?? "Untitled");
  const kind = documentKind(item);
  const { originalUrl, parsedUrl } = resolveDocumentLinks(item);
  const failReason = item.error_detail ? String(item.error_detail) : null;
  const failed =
    FAILED.has(String(item.status ?? "").toLowerCase()) || Boolean(failReason);
  const sourceUrl =
    String(item.content_type ?? "").toLowerCase() === "url"
      ? (item.url ?? null)
      : null;
  const telemetry = readProcessingTelemetry(item.processing_telemetry);

  const copyExtractedText = async () => {
    if (!sections.length) {
      toast.error(
        failed
          ? "There is no extracted text — this document could not be read."
          : "This document has no extracted text yet.",
      );
      return;
    }
    const text = sections.map((section) => section.text).join("\n\n");
    const copied = await copyText(text);
    toast[copied ? "success" : "error"](
      copied
        ? `Extracted text copied — ${sections.length} ${sections.length === 1 ? "section" : "sections"}.`
        : "Could not reach the clipboard.",
    );
  };

  const copyRowJson = async () => {
    const copied = await copyText(rowJson(row));
    toast[copied ? "success" : "error"](
      copied ? "Row copied as JSON." : "Could not reach the clipboard.",
    );
  };

  const copyAddress = async (address: string) => {
    const copied = await copyText(address);
    toast[copied ? "success" : "error"](
      copied ? "Address copied." : "Could not reach the clipboard.",
    );
  };

  return (
    <>
      <div
        className="fixed inset-0 z-55 bg-[rgba(15,17,23,0.35)]"
        onClick={onClose}
        aria-hidden="true"
      />
      <section
        role="dialog"
        aria-modal="true"
        aria-label={`${fileName} — document`}
        data-demo="document-detail"
        className={
          // Below the topbar by default so the collection you are in stays on
          // screen; expanded takes the whole viewport for a long document.
          expanded
            ? "fixed inset-0 z-60 flex flex-col bg-surface"
            : "fixed inset-x-0 top-14 bottom-0 z-60 flex flex-col border-t border-line bg-surface"
        }
      >
        <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
          <h2 className="min-w-0 flex-1 truncate text-[15px] font-bold text-ink">
            {fileName}
          </h2>

          <Btn
            size="sm"
            title="Copy the text we read out of this document"
            onClick={() => void copyExtractedText()}
            startIcon={<Icon name="copy" className="size-3.5" />}
          >
            Copy text
          </Btn>
          {parsedUrl && (
            <Btn
              size="sm"
              aria-label="Download the extracted text"
              title="Download the extracted text"
              onClick={() =>
                void downloadExternalFile(parsedUrl, parsedFileName(fileName))
              }
              startIcon={<Icon name="file" className="size-3.5" />}
            >
              Extracted text
            </Btn>
          )}
          {originalUrl && (
            <Btn
              size="sm"
              variant="ghost"
              aria-label="Download the original file"
              title="Download the file exactly as it was given to us"
              onClick={() => void downloadExternalFile(originalUrl, fileName)}
              startIcon={<Icon name="dn" className="size-3.5" />}
            >
              Original
            </Btn>
          )}
          <Btn
            size="sm"
            title="Copy every field of this row as JSON"
            onClick={() => void copyRowJson()}
            startIcon={<Icon name="copy" className="size-3.5" />}
          >
            JSON
          </Btn>
          <Btn
            size="sm"
            variant="dangerGhost"
            onClick={onDelete}
            startIcon={<Icon name="trash" className="size-3.5" />}
          >
            Delete
          </Btn>

          {positionLabel && (
            <Pill tone="neutral" className="tnum">
              {positionLabel}
            </Pill>
          )}
          <IconBtn
            label="Previous document"
            disabled={!hasPrev}
            onClick={onPrev}
          >
            ‹
          </IconBtn>
          <IconBtn
            data-demo="document-next"
            label="Next document"
            disabled={!hasNext}
            onClick={onNext}
          >
            ›
          </IconBtn>
          <IconBtn
            label={
              expanded ? "Shrink back under the topbar" : "Use the whole screen"
            }
            aria-pressed={expanded}
            onClick={onToggleExpanded}
          >
            <Icon name="expand" className="size-3.5" />
          </IconBtn>
          <IconBtn
            data-demo="document-close"
            label="Close this document"
            onClick={onClose}
          >
            <Icon name="x" className="size-3.5" />
          </IconBtn>
        </header>

        {telemetry && <ProcessingTelemetry telemetry={telemetry} />}

        {/* One column under 1280px: side by side, neither pane is readable on a
            laptop once the field labels and the document text are both in it. */}
        <div className="grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)_minmax(0,1fr)] xl:grid-cols-[minmax(340px,1fr)_minmax(420px,1fr)] xl:grid-rows-1">
          <DocumentViewer
            fileName={fileName}
            kind={kind}
            addedVia={String(row.values.addedvia ?? "File")}
            sourceUrl={sourceUrl}
            originalUrl={originalUrl}
            sections={sections}
            loading={isLoading}
            failed={failed}
            failReason={failReason}
            highlight={highlight}
            onCopyAddress={(address) => void copyAddress(address)}
          />
          <DocumentFields
            row={row}
            fieldColumns={fieldColumns}
            schema={schema}
            sections={sections}
            failReason={failReason}
            onLocate={(match, label) =>
              setLocated({ ...match, objectId: row.objectId, label })
            }
            onCopyJson={() => void copyRowJson()}
          />
        </div>
      </section>
    </>
  );
}
