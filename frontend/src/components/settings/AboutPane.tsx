"use client";

import { useEffect, useState } from "react";

import Icon from "@/components/Icon";
import {
  Btn,
  Card,
  FieldHelp,
  FieldLabel,
  IconBtn,
  Textarea,
} from "@/components/ui";
import {
  useKnowledgeSettings,
  useSaveKnowledgeSettings,
} from "@/data/knowledgeConfig";
import { useToast } from "@/platform/components/ToastContext";

/**
 * What this collection is about, and the id it can be quoted by.
 *
 * The description is the knowledge base's `service_description` — the same
 * sentence processing, extraction and suggested questions read, which is why the
 * artifact asks for it in plain words rather than as a prompt.
 */
export default function AboutPane({ knowledgeId }: { knowledgeId: string }) {
  const { toast } = useToast();
  const { data: config, isLoading } = useKnowledgeSettings(knowledgeId);
  const save = useSaveKnowledgeSettings(knowledgeId);
  const [about, setAbout] = useState("");

  useEffect(() => {
    if (config) setAbout(config.service_description ?? "");
  }, [config]);

  const submit = async () => {
    try {
      // Only the field this screen owns. The save merges it over the stored
      // config, because `PUT …/config` re-applies its own defaults to whatever
      // the body leaves out — see `mergeKnowledgeConfig`.
      const response = await save.mutateAsync({ service_description: about });
      toast.success(response?.message || "Saved");
    } catch {
      // Surfaced by the shared axios interceptor.
    }
  };

  const copyId = async () => {
    try {
      await navigator.clipboard.writeText(knowledgeId);
      toast.success("Knowledge ID copied");
    } catch {
      toast.error("Could not copy the ID");
    }
  };

  if (isLoading) {
    return <p className="text-[13px] text-ink-2">Reading this collection…</p>;
  }

  return (
    <div className="max-w-[760px]">
      <div className="mb-4">
        <FieldLabel htmlFor="di-about">
          What are these documents about?
        </FieldLabel>
        <Textarea
          id="di-about"
          value={about}
          onChange={(event) => setAbout(event.target.value)}
          placeholder="e.g. Extracted candidate profiles from uploaded resumes — skills, experience, current company, location, and availability are important."
        />
        <FieldHelp>
          This context helps with processing, extraction, search, and suggested
          questions.
        </FieldHelp>
      </div>

      <Card className="bg-sunken px-4 py-3.5">
        <b className="text-[13px] text-ink">Built for you, tunable by us</b>
        <p className="m-0 mt-1.5 text-[12.5px] leading-[1.6] text-ink-2">
          This collection works well out of the box — search and organization
          are tuned automatically. For specialized needs, our team can customize
          how your documents are processed and searched, based on your
          requirements.
        </p>
      </Card>

      <div className="mt-3.5 grid grid-cols-[150px_1fr] gap-3.5 py-2.5 text-[13px]">
        <div className="text-ink-3">Knowledge ID</div>
        <div className="flex items-center gap-2 font-mono text-[12px] text-ink-3">
          {knowledgeId}
          <IconBtn
            label="Copy Knowledge ID"
            onClick={copyId}
            className="size-5"
          >
            <Icon name="copy" className="size-3" />
          </IconBtn>
        </div>
      </div>

      <div className="mt-5">
        <Btn variant="primary" onClick={submit} disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save changes"}
        </Btn>
      </div>
    </div>
  );
}
