"use client";

import { useState } from "react";

import { EmptyNote, PageHead } from "@/components/ui";
import { useCollection } from "@/data/collections";
import { useAuth } from "@/platform/hooks/useAuth";
import { cn } from "@/platform/utils";

import AboutPane from "./AboutPane";
import FieldsPane from "./FieldsPane";
import GeneralPane from "./GeneralPane";

type Tab = "general" | "about" | "fields";

const tabs: Array<{ value: Tab; label: string }> = [
  { value: "general", label: "General" },
  { value: "about", label: "About this collection" },
  { value: "fields", label: "Extracted fields" },
];

export default function SettingsView() {
  const { agentId, knowledgeId } = useAuth();
  // The knowledge base arrives with the collection; before that lands, the two
  // knowledge-backed tabs are loading rather than empty.
  const { isLoading: isCollectionLoading } = useCollection(
    agentId ?? undefined,
  );
  const [tab, setTab] = useState<Tab>("general");

  const loadingNote = (
    <p className="text-[13px] text-ink-2">Opening this collection…</p>
  );

  return (
    <>
      <PageHead
        title="Settings"
        sub="Update what this collection is about and which details it pulls out of your documents."
      />

      <div className="mb-5 flex gap-0.5 border-b border-line">
        {tabs.map((entry) => (
          <button
            key={entry.value}
            type="button"
            onClick={() => setTab(entry.value)}
            aria-current={tab === entry.value ? "page" : undefined}
            data-demo={`settings-tab-${entry.value}`}
            className={cn(
              "mr-[22px] cursor-pointer border-0 border-b-2 bg-transparent px-1 py-2.5 text-[13.5px] font-semibold",
              tab === entry.value
                ? "border-accent text-accent"
                : "border-transparent text-ink-2 hover:text-ink",
            )}
          >
            {entry.label}
          </button>
        ))}
      </div>

      {tab === "general" &&
        (agentId ? (
          <GeneralPane collectionId={agentId} />
        ) : (
          <EmptyNote>This collection could not be read.</EmptyNote>
        ))}

      {tab === "about" &&
        (knowledgeId ? (
          <AboutPane knowledgeId={knowledgeId} />
        ) : isCollectionLoading ? (
          loadingNote
        ) : (
          <EmptyNote>
            This collection has no documents attached yet — add some from
            Knowledge.
          </EmptyNote>
        ))}

      {tab === "fields" &&
        (knowledgeId ? (
          <FieldsPane knowledgeId={knowledgeId} />
        ) : isCollectionLoading ? (
          loadingNote
        ) : (
          <EmptyNote>
            Fields are extracted from documents, and this collection has none
            yet.
          </EmptyNote>
        ))}
    </>
  );
}
