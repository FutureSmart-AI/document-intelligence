"use client";

import { EmptyNote, PageHead } from "@/components/ui";
import { useCollection } from "@/data/collections";
import { ExploreView } from "@/platform/components/rag/dashboard/explore";
import { useAuth } from "@/platform/hooks/useAuth";

/**
 * Explore — every value a detail takes across the collection, with counts.
 *
 * The field chips, the counted value list and the documents behind a value are
 * the agent UI's `ExploreView`, which reads the same metadata route the Data
 * table does; this is the product's framing around it.
 */
export default function ExploreScreenBody() {
  const { agentId, knowledgeId } = useAuth();
  // Until the collection itself has loaded, "no documents" is not yet a fact.
  const { isLoading } = useCollection(agentId ?? undefined);

  return (
    <>
      <PageHead
        title="Explore"
        sub="See every value a field takes across your documents, and how many documents carry it."
      />
      {knowledgeId ? (
        <ExploreView />
      ) : isLoading ? (
        <p className="text-[13px] text-ink-2">Opening this collection…</p>
      ) : (
        <EmptyNote>
          This collection has no documents attached yet — add some from
          Knowledge.
        </EmptyNote>
      )}
    </>
  );
}
