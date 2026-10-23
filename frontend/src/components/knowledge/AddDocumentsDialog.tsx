"use client";

import { useRef, useState } from "react";

import Icon from "@/components/Icon";
import {
  Btn,
  FieldHelp,
  FieldLabel,
  IconBtn,
  Input,
  Modal,
  Select,
  Textarea,
} from "@/components/ui";
import { ingestFiles, ingestPlainText, ingestWebsite } from "@/data/ingest";
import {
  DEFAULT_MAX_DEPTH,
  MAX_DEPTH_OPTIONS,
  SCRAPE_METHODS,
  type MaxDepth,
  type ScrapeMethod,
} from "@/lib/constants";
import { readableSize } from "@/lib/fileSize";
import { useApiError } from "@/lib/useApiError";
import { ClickUpWizard } from "@/platform/components/rag/dashboard/knowledge/clickup";
import type { BatchUploadError } from "@/platform/components/rag/dashboard/knowledge/hooks/useFileUpload.types";
import {
  ACCEPTED_TYPES_LABEL,
  MAX_UPLOAD_SIZE_BYTES,
} from "@/platform/components/rag/dashboard/knowledge/utils/fileUtils";
import { useToast } from "@/platform/components/ToastContext";

import type { IngestSource } from "./AddKnowledgeTiles";

const titles: Record<IngestSource, string> = {
  file: "Upload files",
  plain_text: "Add plain text",
  url: "Add a website",
  clickup: "Sources & sync",
};

/** A URL the browser itself can parse, and that names an http(s) host. */
const isUrl = (value: string): boolean => {
  try {
    const parsed = new URL(value.trim());
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
};

/**
 * Adding documents to the collection being looked at.
 *
 * This product's own dialog rather than the platform's, for one reason: the
 * add-document contract is small — scrape-or-crawl and its depth for a website,
 * nothing at all for files or plain text — and every other field is the
 * service's own business (see `data/ingest`). The platform's dialog asks for
 * chunking, chunk size, a document-processing type, a transcript provider and a
 * parser toggle, none of which this product exposes or sends: the service
 * detects how a document has to be read, so the processing type is not a
 * question with a person's answer.
 *
 * ClickUp is the exception: its four-step connect wizard is the platform's, and
 * it talks to the ClickUp routes rather than the add-document one, so there is
 * nothing to trim and no reason to rebuild it.
 */
export default function AddDocumentsDialog({
  knowledgeId,
  source,
  onClose,
  onDone,
  onBatchErrors,
}: {
  knowledgeId: string;
  source: IngestSource;
  /** Back out to the tiles — this dialog opens from one, so Back is Close. */
  onClose: () => void;
  /** Something was queued: refresh the list. */
  onDone: () => void;
  /** Files the service took but could not read, reported above the list. */
  onBatchErrors?: (errors: BatchUploadError[]) => void;
}) {
  const { toast } = useToast();
  const reportError = useApiError();

  const [files, setFiles] = useState<File[]>([]);
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [method, setMethod] = useState<ScrapeMethod>("scrape");
  const [maxDepth, setMaxDepth] = useState<MaxDepth>(DEFAULT_MAX_DEPTH);
  const [busy, setBusy] = useState(false);
  const [percent, setPercent] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);

  const sizeLimit = MAX_UPLOAD_SIZE_BYTES;

  const close = () => {
    if (busy) return;
    onClose();
  };

  const pickFiles = (picked: File[]) => {
    const tooBig = picked.find((file) => file.size > sizeLimit);
    if (tooBig) {
      toast.error(
        `${tooBig.name} is ${readableSize(tooBig.size)} — the limit is ${readableSize(sizeLimit)} per file.`,
      );
      return;
    }
    if (files.length + picked.length > 50) {
      toast.error(
        `Up to 50 files at a time. You have ${files.length} selected and picked ${picked.length} more.`,
      );
      return;
    }
    setFiles((current) => [...current, ...picked]);
  };

  const submitFiles = async () => {
    setBusy(true);
    setPercent(0);
    try {
      const { failed, invalid } = await ingestFiles(knowledgeId, files, {
        onProgress: setPercent,
      });

      if (failed.length) {
        const names = failed.map((entry) => entry.fileName).join(", ");
        toast.error(
          `${failed.length} file${failed.length === 1 ? "" : "s"} could not be sent: ${names}`,
        );
      }
      if (invalid.length && onBatchErrors) {
        onBatchErrors(
          invalid.map((entry) => ({
            file_name: entry.fileName,
            error_detail: entry.errorDetail ?? "This file could not be read.",
          })),
        );
      }
      const queued = files.length - failed.length - invalid.length;
      if (queued > 0) {
        toast.success(
          `${queued} document${queued === 1 ? "" : "s"} added — they will be ready shortly.`,
        );
      }
      onDone();
      onClose();
    } catch (error) {
      // Nothing else reports this — the axios instance handles 401 only — and a
      // full account is refused here, so say which allowance ran out.
      reportError(error, "Those files could not be added.");
    } finally {
      setBusy(false);
      setPercent(0);
    }
  };

  const submitText = async () => {
    setBusy(true);
    try {
      await ingestPlainText(knowledgeId, text);
      toast.success("Text added — it will be ready shortly.");
      onDone();
      onClose();
    } catch (error) {
      reportError(error, "That text could not be added.");
    } finally {
      setBusy(false);
    }
  };

  const submitUrl = async () => {
    setBusy(true);
    try {
      await ingestWebsite(knowledgeId, url.trim(), { method, maxDepth });
      toast.success(
        method === "crawl"
          ? "Website added — the page and the ones it links to will be ready shortly."
          : "Website added — it will be ready shortly.",
      );
      onDone();
      onClose();
    } catch (error) {
      reportError(error, "That website could not be added.");
    } finally {
      setBusy(false);
    }
  };

  // The wizard brings its own footer and its own steps.
  if (source === "clickup") {
    return (
      <Modal open onClose={close} title={titles.clickup} width="720px">
        <ClickUpWizard
          onSuccess={() => {
            onDone();
            onClose();
          }}
          onCancel={close}
        />
      </Modal>
    );
  }

  const ready =
    source === "file"
      ? files.length > 0
      : source === "plain_text"
        ? text.trim().length >= 6
        : isUrl(url);

  const submit =
    source === "file"
      ? submitFiles
      : source === "plain_text"
        ? submitText
        : submitUrl;

  return (
    <Modal
      open
      onClose={close}
      title={titles[source]}
      footer={
        <>
          <Btn onClick={close} disabled={busy}>
            Cancel
          </Btn>
          <Btn variant="primary" onClick={submit} disabled={!ready || busy}>
            {busy ? "Adding…" : "Add"}
          </Btn>
        </>
      }
    >
      {source === "file" && (
        <>
          <input
            ref={fileInput}
            type="file"
            multiple
            accept=".pdf,.docx,.txt,.mp3,.md"
            className="hidden"
            disabled={busy}
            onChange={(event) => {
              pickFiles(Array.from(event.target.files ?? []));
              event.target.value = "";
            }}
          />
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            disabled={busy}
            className="w-full cursor-pointer rounded-di-lg border-[1.5px] border-dashed border-line-strong px-4 py-6 text-center text-[13px] text-ink-2 hover:border-accent disabled:cursor-not-allowed"
          >
            <b className="mb-1 block text-ink">
              {files.length
                ? `Add more files (${files.length} selected)`
                : "Click to choose files"}
            </b>
            {ACCEPTED_TYPES_LABEL} · up to {readableSize(sizeLimit)} each · 50
            files at a time
          </button>

          {files.length > 0 && (
            <div className="mt-3 max-h-52 overflow-y-auto rounded-di-md border border-line bg-sunken p-2">
              {files.map((file, index) => (
                <div
                  key={`${file.name}-${index}`}
                  className="flex items-center justify-between gap-2 rounded-di-md bg-surface px-3 py-2"
                >
                  <span className="min-w-0">
                    <b className="block truncate text-[13px] text-ink">
                      {file.name}
                    </b>
                    <span className="text-[11.5px] text-ink-2">
                      {readableSize(file.size)}
                    </span>
                  </span>
                  <IconBtn
                    label={`Remove ${file.name}`}
                    onClick={() =>
                      setFiles((current) =>
                        current.filter((_, position) => position !== index),
                      )
                    }
                  >
                    <Icon name="x" className="size-4" />
                  </IconBtn>
                </div>
              ))}
            </div>
          )}

          {busy && (
            <div className="mt-3">
              <div className="h-2 w-full overflow-hidden rounded-full bg-line">
                <div
                  className="h-2 rounded-full bg-accent transition-[width]"
                  style={{ width: `${percent}%` }}
                />
              </div>
              <FieldHelp>{percent}% sent</FieldHelp>
            </div>
          )}
        </>
      )}

      {source === "plain_text" && (
        <>
          <FieldLabel htmlFor="di-add-text">Content</FieldLabel>
          <Textarea
            id="di-add-text"
            value={text}
            onChange={(event) => setText(event.target.value)}
            className="min-h-[150px]"
            placeholder="Type or paste your content here…"
          />
          <FieldHelp>
            At least a few words — notes, an email thread, anything you have as
            text.
          </FieldHelp>
        </>
      )}

      {source === "url" && (
        <>
          <FieldLabel required htmlFor="di-add-url">
            Website URL
          </FieldLabel>
          <Input
            id="di-add-url"
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://example.com/page"
          />
          {url.trim() && !isUrl(url) && (
            <FieldHelp>
              That does not look like a full address — it needs to start with
              http:// or https://
            </FieldHelp>
          )}

          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <div>
              <FieldLabel htmlFor="di-scrape-method">What to read</FieldLabel>
              <Select
                id="di-scrape-method"
                value={method}
                onChange={(event) =>
                  setMethod(event.target.value as ScrapeMethod)
                }
              >
                {SCRAPE_METHODS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </div>
            {/* Depth stays on screen either way so the control is findable,
                but it only means something for a crawl: a scrape reads the one
                page it was given, so the select is disabled there and nothing
                is sent for it. */}
            <div>
              <FieldLabel htmlFor="di-max-depth">How many levels</FieldLabel>
              <Select
                id="di-max-depth"
                value={String(maxDepth)}
                disabled={method !== "crawl"}
                className="disabled:cursor-not-allowed disabled:opacity-55"
                onChange={(event) =>
                  setMaxDepth(Number(event.target.value) as MaxDepth)
                }
              >
                {MAX_DEPTH_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          <FieldHelp>
            Scrape reads the page you gave us. Crawl also reads the pages it
            links to — how many levels says how far it follows them.
          </FieldHelp>
        </>
      )}
    </Modal>
  );
}
