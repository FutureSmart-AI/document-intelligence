import { useState } from "react";

import { Loader2, RefreshCw } from "lucide-react";

import { useToast } from "@/platform/components/ToastContext";
import { Button, Input } from "@/platform/components/ui";

import { useResyncDoc } from "./hooks/useClickUpApi";
import type { Selection } from "./types";
import { parseDocId } from "./utils";

interface ResyncDocSectionProps {
  selection: Selection;
}

/**
 * Re-index a single ClickUp doc on demand — for when new content was added to a
 * doc and you want it refreshed without waiting for the next scheduled sync.
 *
 * The backend exposes no per-doc listing endpoint, so docs are targeted by id:
 * pinned docs (`space_doc_allowlist`) get one-click rows, and any other doc can
 * be resynced by pasting its URL or Doc ID.
 */
export const ResyncDocSection = ({ selection }: ResyncDocSectionProps) => {
  const resyncDoc = useResyncDoc();
  const { toast } = useToast();
  const [input, setInput] = useState("");
  // Track which doc is in-flight so the spinner lands on the right row/button.
  const [activeDocId, setActiveDocId] = useState<string | null>(null);

  const pinnedDocs = selection.space_doc_allowlist ?? [];

  const resync = (docId: string, onSuccess?: () => void) => {
    setActiveDocId(docId);
    resyncDoc.mutate(docId, {
      onSuccess: () => onSuccess?.(),
      onSettled: () => setActiveDocId(null),
    });
  };

  const submitManual = () => {
    const docId = parseDocId(input);
    if (!docId) {
      toast.error("Enter a valid ClickUp doc URL or Doc ID");
      return;
    }
    // Keep the input on failure so a rejected resync can be retried.
    resync(docId, () => setInput(""));
  };

  return (
    <div>
      <div className="mb-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
          Resync a doc
        </span>
      </div>
      <p className="mb-2 text-xs text-slate-500">
        Re-index a single doc after adding new content to it — no need to wait
        for the next scheduled sync.
      </p>

      {/* Manual resync by URL / Doc ID */}
      <div className="flex gap-2">
        <Input
          size="sm"
          fullWidth
          value={input}
          onChange={setInput}
          placeholder="Paste doc URL or Doc ID (e.g. abc12de-3456)"
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              submitManual();
            }
          }}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={submitManual}
          disabled={!input.trim() || resyncDoc.isPending}
          startIcon={<RefreshCw className="size-3.5" />}
        >
          Resync
        </Button>
      </div>

      {/* Quick-resync rows for pinned docs */}
      {pinnedDocs.length > 0 && (
        <div className="mt-2 overflow-hidden rounded-lg border border-slate-200">
          {pinnedDocs.map((docId) => {
            const isActive = activeDocId === docId && resyncDoc.isPending;
            return (
              <div
                key={docId}
                className="flex items-center justify-between gap-3 border-b border-slate-50 px-3 py-2 text-xs last:border-b-0"
              >
                <span className="truncate font-mono text-slate-600">
                  {docId}
                </span>
                <Button
                  variant="ghost"
                  size="xs"
                  onClick={() => resync(docId)}
                  disabled={resyncDoc.isPending}
                  startIcon={
                    isActive ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <RefreshCw className="size-3.5" />
                    )
                  }
                >
                  Resync
                </Button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
