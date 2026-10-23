"use client";

import { useState } from "react";

import Icon from "@/components/Icon";
import type { KnowledgeItem } from "@/platform/hooks/useKnowledgeData";

import { DOT, health, KIND } from "./constants";

interface DiDocumentsPanelProps {
  /** The collection the documents belong to, named in the panel's header. */
  collectionName?: string;
  documents: KnowledgeItem[];
  isLoading: boolean;
  error?: string | null;
  onClose: () => void;
}

/** What a document is called: its file name, or the address it was read from. */
const label = (document: KnowledgeItem): string =>
  document.file_name || document.url || document.object_id;

/**
 * The documents behind the answers, beside the thread.
 *
 * The Database Agent's screen opens onto the tables a question can reach; a
 * collection's equivalent is the documents it may read. Each row opens onto what
 * the service actually knows about one — where it came from, when it was added,
 * and why it failed when it did — rather than inventing a page or chunk listing
 * the list route does not carry.
 */
export default function DiDocumentsPanel({
  collectionName,
  documents,
  isLoading,
  error,
  onClose,
}: DiDocumentsPanelProps) {
  const [openDocument, setOpenDocument] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const needle = query.trim().toLowerCase();
  const shown = needle
    ? documents.filter((document) =>
        label(document).toLowerCase().includes(needle),
      )
    : documents;

  return (
    <aside
      aria-label="Collection documents"
      className="flex w-[272px] flex-none flex-col border-l border-line bg-surface"
    >
      <div className="flex items-center gap-2 border-b border-line px-3.5 py-3">
        <h2 className="text-[12.5px] font-bold text-ink">Documents</h2>
        {collectionName && (
          <span className="truncate font-mono text-[10.5px] text-ink-3">
            {collectionName}
          </span>
        )}
        <div className="flex-1" />
        <button
          type="button"
          onClick={onClose}
          aria-label="Hide documents"
          className="grid cursor-pointer place-items-center border-0 bg-transparent text-ink-3 hover:text-ink"
        >
          <Icon name="x" className="size-3.5" />
        </button>
      </div>

      {documents.length > 0 && (
        <div className="flex items-center gap-1.5 border-b border-line px-3 py-2">
          <Icon name="search" className="size-3 flex-none text-ink-3" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search documents"
            aria-label="Search documents"
            className="w-full border-0 bg-transparent text-xs text-ink outline-none placeholder:text-ink-3"
          />
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-2">
        {error ? (
          <p className="px-2 py-1 text-xs text-danger-text">{error}</p>
        ) : isLoading ? (
          <div className="flex flex-col gap-1.5 px-1 pt-1">
            {[0, 1, 2, 3].map((row) => (
              <div
                key={row}
                className="h-7 animate-pulse rounded-di-md bg-sunken"
              />
            ))}
          </div>
        ) : !shown.length ? (
          <p className="px-2 py-1 text-xs text-ink-3">
            {needle
              ? `No document matches “${query.trim()}”.`
              : "This collection holds no documents yet."}
          </p>
        ) : (
          shown.map((document) => {
            const open = openDocument === document.object_id;
            const state = health(document.status);
            return (
              <div key={document.object_id} className="flex flex-col">
                <button
                  type="button"
                  aria-expanded={open}
                  onClick={() =>
                    setOpenDocument(open ? null : document.object_id)
                  }
                  className={`flex cursor-pointer items-center gap-2 rounded-di-md border-0 px-2.5 py-2 text-left hover:bg-sunken ${
                    open ? "bg-sunken" : "bg-transparent"
                  }`}
                >
                  <Icon
                    name="caretRight"
                    className={`size-3 flex-none text-ink-3 transition-transform duration-150 ${
                      open ? "rotate-90" : ""
                    }`}
                  />
                  <span
                    className={`truncate text-xs ${
                      open
                        ? "font-semibold text-accent-strong"
                        : "font-medium text-ink-2"
                    }`}
                  >
                    {label(document)}
                  </span>
                  <div className="flex-1" />
                  <span
                    title={document.status}
                    aria-label={document.status}
                    className={`size-[7px] flex-none rounded-full ${DOT[state]}`}
                  />
                </button>
                {open && (
                  <div className="flex flex-col gap-1 pt-0.5 pr-2 pb-2 pl-7">
                    <p className="m-0 text-[11.5px] text-ink-2">
                      {KIND[document.content_type] ?? document.content_type} ·{" "}
                      {document.status}
                    </p>
                    {document.created_time && (
                      <p className="m-0 text-[11px] text-ink-3">
                        Added{" "}
                        {new Date(document.created_time).toLocaleDateString()}
                      </p>
                    )}
                    {document.url && (
                      <p className="m-0 truncate font-mono text-[10.5px] text-ink-3">
                        {document.url}
                      </p>
                    )}
                    {state === "failed" && (
                      <p className="m-0 text-[11px] text-danger-text">
                        {document.error_detail ||
                          "This document could not be read, so answers never draw on it."}
                      </p>
                    )}
                    {state === "working" && (
                      <p className="m-0 text-[11px] text-warn-text">
                        Still being read — answers do not draw on it yet.
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
