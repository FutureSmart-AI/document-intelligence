"use client";

import { useRef } from "react";

import Icon from "@/components/Icon";
import {
  FieldHelp,
  FieldLabel,
  Input,
  Select,
  Textarea,
} from "@/components/ui";
import type { CreateStep } from "@/data/createCollection";
import {
  MAX_DEPTH_OPTIONS,
  SCRAPE_METHODS,
  type MaxDepth,
  type ScrapeMethod,
} from "@/lib/constants";
import { cn } from "@/platform/utils";

export type DocumentSource = "upload" | "plaintext" | "url";

/**
 * Step three: the documents, or an empty collection you feed yourself.
 *
 * Both halves are offered side by side because both are normal. Most
 * collections in use are fed by something else — a form, a mailbox, a drive
 * folder — and a flow that treats "I have no files right now" as the unhappy
 * path pushes people into uploading a sample they do not care about just to get
 * through it.
 */
export default function StepDocuments({
  collectionName,
  fieldCount,
  source,
  onSourceChange,
  files,
  onFilesChange,
  text,
  onTextChange,
  url,
  onUrlChange,
  method,
  onMethodChange,
  maxDepth,
  onMaxDepthChange,
  busy,
  progress,
  hasDocuments,
}: {
  collectionName: string;
  fieldCount: number;
  source: DocumentSource;
  onSourceChange: (next: DocumentSource) => void;
  files: File[];
  onFilesChange: (next: File[]) => void;
  text: string;
  onTextChange: (next: string) => void;
  url: string;
  onUrlChange: (next: string) => void;
  method: ScrapeMethod;
  onMethodChange: (next: ScrapeMethod) => void;
  maxDepth: MaxDepth;
  onMaxDepthChange: (next: MaxDepth) => void;
  busy: boolean;
  progress: CreateStep | null;
  hasDocuments: boolean;
}) {
  const fileInput = useRef<HTMLInputElement>(null);

  return (
    <>
      <p className="mb-3.5 text-[12.5px] text-ink-2">
        The {fieldCount} field{fieldCount === 1 ? "" : "s"} you confirmed are
        ready. Every document you add to{" "}
        <b className="text-ink">{collectionName}</b> — now or in a year — is
        read the same way.
      </p>

      <div className="grid gap-3.5 lg:grid-cols-2">
        <div className="overflow-hidden rounded-di-md border border-line">
          <div className="border-b border-line bg-sunken px-3 py-2.25 text-[12px] font-bold text-ink">
            Add them now
          </div>
          <div className="p-3.5">
            <div
              className="mb-2.5 inline-flex overflow-hidden rounded-di-md border border-line-strong"
              role="group"
              aria-label="Where the documents come from"
            >
              {(
                [
                  ["upload", "Files"],
                  ["plaintext", "Text"],
                  ["url", "Website"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  data-demo={`documents-source-${value}`}
                  aria-pressed={source === value}
                  disabled={busy}
                  onClick={() => onSourceChange(value)}
                  className={cn(
                    "cursor-pointer px-3 py-1.5 text-[12.5px] font-semibold disabled:cursor-not-allowed",
                    source === value
                      ? "bg-accent-soft text-accent-softtext"
                      : "bg-surface text-ink-2 hover:bg-sunken",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>

            {source === "upload" && (
              <>
                <input
                  ref={fileInput}
                  type="file"
                  multiple
                  className="hidden"
                  onChange={(event) =>
                    onFilesChange(Array.from(event.target.files ?? []))
                  }
                />
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => fileInput.current?.click()}
                  className="w-full cursor-pointer rounded-di-lg border-[1.5px] border-dashed border-line-strong bg-sunken px-4 py-6 text-center text-[12.5px] text-ink-2 hover:border-accent hover:bg-accent-soft disabled:cursor-not-allowed"
                >
                  <Icon
                    name="up"
                    className="mx-auto mb-1.5 size-6.5 text-ink-3"
                  />
                  <b className="mb-0.75 block text-[13.5px] text-ink">
                    {files.length
                      ? `${files.length} file${files.length === 1 ? "" : "s"} ready to send`
                      : "Drag your documents here, or click to browse"}
                  </b>
                  As many as you like · up to 50MB each
                </button>
                {files.length > 0 && (
                  <FieldHelp>
                    {files.map((file) => file.name).join(", ")}
                  </FieldHelp>
                )}
              </>
            )}

            {source === "plaintext" && (
              <Textarea
                value={text}
                disabled={busy}
                onChange={(event) => onTextChange(event.target.value)}
                className="min-h-27.5"
                placeholder="Type or paste your content here…"
              />
            )}

            {source === "url" && (
              <>
                <Input
                  type="url"
                  value={url}
                  disabled={busy}
                  onChange={(event) => onUrlChange(event.target.value)}
                  placeholder="https://example.com/page"
                />
                <div className="mt-2.5 grid gap-2.5 sm:grid-cols-2">
                  <div>
                    <FieldLabel htmlFor="di-create-method">
                      What to read
                    </FieldLabel>
                    <Select
                      id="di-create-method"
                      value={method}
                      disabled={busy}
                      onChange={(event) =>
                        onMethodChange(event.target.value as ScrapeMethod)
                      }
                    >
                      {SCRAPE_METHODS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </Select>
                  </div>
                  {/* Depth stays on screen either way so the control is
                      findable, but it only means something for a crawl — a
                      scrape reads the one page it was given. */}
                  <div>
                    <FieldLabel htmlFor="di-create-depth">
                      How many levels
                    </FieldLabel>
                    <Select
                      id="di-create-depth"
                      value={String(maxDepth)}
                      disabled={busy || method !== "crawl"}
                      className="disabled:cursor-not-allowed disabled:opacity-55"
                      onChange={(event) =>
                        onMaxDepthChange(Number(event.target.value) as MaxDepth)
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
              </>
            )}
          </div>
        </div>

        <div className="overflow-hidden rounded-di-md border border-line">
          <div className="border-b border-line bg-sunken px-3 py-2.25 text-[12px] font-bold text-ink">
            Or send them from your own code
          </div>
          <div className="p-3.5">
            <p className="m-0 mb-2.5 text-[12px] leading-relaxed text-ink-2">
              Most collections are fed automatically — a form, a mailbox, a
              drive folder or a chat app calling the API as documents arrive.
              Create the collection empty and point your automation at it.
            </p>
            <p className="m-0 text-[12px] leading-relaxed text-ink-2">
              <b className="text-ink">Create it empty</b> and the API key is
              waiting for you under <b className="text-ink">Share</b>.
            </p>
            {/* Said here rather than left for someone to discover: the first
                document is the first time these fields are actually tried,
                because this API has no way to read a document for its fields
                without a collection to read it into. */}
            <p className="m-0 mt-2.5 rounded-di-md border border-line-soft bg-sunken px-2.75 py-2.25 text-[11.5px] leading-relaxed text-ink-2">
              The first document you add is where you find out how these fields
              do in practice. Open <b className="text-ink">Knowledge</b> once it
              has been read: anything that came back empty is marked, and the
              wording behind it is yours to change in Settings at any time.
            </p>
          </div>
        </div>
      </div>

      {busy && (
        <div className="mt-3.5 flex flex-col gap-2">
          {(
            [
              ["collection", "Setting up your collection"],
              ["fields", "Saving the fields you confirmed"],
              [
                "documents",
                hasDocuments
                  ? "Reading your documents and filling the table"
                  : "Finishing up",
              ],
            ] as Array<[CreateStep, string]>
          ).map(([value, label]) => {
            const order: CreateStep[] = ["collection", "fields", "documents"];
            const done =
              order.indexOf(value) < order.indexOf(progress ?? "collection");
            const active = progress === value;
            return (
              <div
                key={value}
                className={cn(
                  "flex items-center gap-2.5 text-[13px]",
                  done || active ? "text-ink" : "text-ink-3",
                  active && "font-semibold",
                )}
              >
                <span
                  className={cn(
                    "flex size-4 items-center justify-center rounded-full border-2",
                    done
                      ? "border-success-dot bg-success-dot"
                      : active
                        ? "border-accent"
                        : "border-line-strong",
                  )}
                >
                  {done && <Icon name="chk" className="size-2.5 text-white" />}
                </span>
                {label}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
