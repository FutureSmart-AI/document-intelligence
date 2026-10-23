"use client";

import { EmptyNote, ToggleRow } from "@/components/ui";
import {
  useKnowledgeSettings,
  useSaveKnowledgeSettings,
} from "@/data/knowledgeConfig";
import { useToast } from "@/platform/components/ToastContext";

import DocumentFieldsPanel from "./fields/DocumentFieldsPanel";

/**
 * The details pulled out of every document — and the switch that decides whether
 * any are.
 *
 * "Enable structured extraction" is the knowledge base's `enrichment_enabled`
 * flag, which the service derives both metadata levels from: off, documents are
 * still indexed for search, chat and citations, and nothing is extracted, so
 * Data has no table to show. The fields table itself is the agent UI's own
 * enrichment page, which owns the add/edit/import flows and the protected system
 * fields — read in this product's terms through `typeMode="resolved"`.
 */
export default function FieldsPane({ knowledgeId }: { knowledgeId: string }) {
  const { toast } = useToast();
  const { data: config, isLoading } = useKnowledgeSettings(knowledgeId);
  const save = useSaveKnowledgeSettings(knowledgeId);

  // The flag is the switch; the levels are what the service sets from it, and
  // are read only as the fallback for a base configured before it did.
  const documentLevel = Boolean(
    config?.enrichment_enabled ??
    config?.chunking_strategy?.document_level_metadata,
  );
  const sectionLevel = Boolean(
    config?.chunking_strategy?.chunk_level_metadata ?? documentLevel,
  );

  const toggleExtraction = async (next: boolean) => {
    try {
      // One flag is the whole body: the service turns both metadata levels off
      // with it, so the levels are no longer this screen's to send.
      const response = await save.mutateAsync({ enrichment_enabled: next });
      toast.success(
        response?.message ||
          (next
            ? "Structured extraction is on for this collection."
            : "Structured extraction is off — documents stay indexed for search."),
      );
    } catch {
      // Surfaced by the shared axios interceptor.
    }
  };

  if (isLoading) {
    return <p className="text-[13px] text-ink-2">Reading this collection…</p>;
  }

  return (
    <div>
      <ToggleRow
        className="mb-4.5 border-b border-line-soft pt-0 pb-4"
        label="Enable structured extraction"
        desc="Off — your documents are simply made searchable so you can search, chat, and see citations, with no fields extracted. On — we also pull out the specific fields you define below, so chat can give exact answers from your data and every document becomes viewable as a table under Data."
        checked={documentLevel}
        onChange={toggleExtraction}
        disabled={save.isPending}
      />

      {documentLevel ? (
        // This product's own fields screen, written in the words of the beta
        // design rather than the platform's: a four-column table and a dialog
        // per field, over the same hooks the shared page uses. Both levels are
        // passed through because the service still keeps section fields and a
        // collection created with one has to be able to see it.
        <DocumentFieldsPanel
          knowledgeId={knowledgeId}
          documentMetadataEnabled={documentLevel}
          chunkMetadataEnabled={sectionLevel}
        />
      ) : (
        <EmptyNote>
          Structured extraction is off for this collection — documents are made
          searchable only. Search, chat, and citations are still fully
          available; no fields are extracted and there&rsquo;s no table view in
          Data.
        </EmptyNote>
      )}
    </div>
  );
}
