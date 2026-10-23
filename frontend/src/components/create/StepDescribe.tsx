"use client";

import Icon from "@/components/Icon";
import { FieldHelp, FieldLabel, Input, Textarea } from "@/components/ui";
import type { FieldTemplate } from "@/data/fieldCatalog";
import { cn } from "@/platform/utils";

import { iconFor } from "./constants";

/**
 * Step one: what these documents are, and what you want out of them.
 *
 * The kinds on offer are the service's (`GET /v1/field-templates`), not a list
 * this screen ships. Picking one hands step two that template's published
 * fields — already named, described and typed — with no further call, which is
 * why a template is the fast path and typing a description is the general one.
 *
 * Nothing is uploaded here. Describing a collection is not something anyone
 * should need a file to do, and the one document this flow reads is picked on
 * the next step, where its results are on screen beside the fields it was read
 * with.
 */

export default function StepDescribe({
  templates,
  templatesLoading,
  templatesFailed,
  template,
  onPickTemplate,
  name,
  onNameChange,
  about,
  onAboutChange,
  wanted,
  onWantedChange,
}: {
  templates: FieldTemplate[];
  templatesLoading?: boolean;
  templatesFailed?: boolean;
  template: string | null;
  onPickTemplate: (key: string) => void;
  name: string;
  onNameChange: (next: string) => void;
  about: string;
  onAboutChange: (next: string) => void;
  wanted: string;
  onWantedChange: (next: string) => void;
}) {
  return (
    <>
      <div className="mb-3.5">
        <FieldLabel>What kind of documents are these?</FieldLabel>
        <FieldHelp className="mt-0 mb-2">
          Pick the closest one and we will start you off with the fields people
          usually want — or describe it yourself below. Either way you see every
          field before anything is created.
        </FieldHelp>

        {templatesLoading ? (
          <div
            role="status"
            aria-label="Loading the kinds of collection"
            className="grid gap-2.25 sm:grid-cols-2 xl:grid-cols-3"
          >
            {[0, 1, 2].map((slot) => (
              <div
                key={slot}
                className="h-13.5 animate-pulse rounded-di-md border border-line bg-sunken"
              />
            ))}
          </div>
        ) : templatesFailed || !templates.length ? (
          // Not an error state to block on: describing the collection in your
          // own words is the general path and needs none of this.
          <FieldHelp className="mt-0">
            We could not load the ready-made kinds just now — describe these
            documents below instead and we will work the fields out from that.
          </FieldHelp>
        ) : (
          <div className="grid gap-2.25 sm:grid-cols-2 xl:grid-cols-3">
            {templates.map((entry) => (
              <button
                key={entry.key}
                type="button"
                data-demo={`template-${entry.key}`}
                aria-pressed={template === entry.key}
                onClick={() => onPickTemplate(entry.key)}
                className={cn(
                  "flex cursor-pointer items-start gap-2.25 rounded-di-md border px-3 py-2.5 text-left transition-colors",
                  template === entry.key
                    ? "border-accent bg-accent-soft"
                    : "border-line bg-sunken hover:border-accent hover:bg-accent-soft",
                )}
              >
                <span className="flex size-7.5 flex-none items-center justify-center rounded-lg border border-line bg-surface text-accent">
                  <Icon name={iconFor(entry.key)} className="size-4.25" />
                </span>
                <span className="min-w-0">
                  <b className="block text-[13px] text-ink">
                    {entry.display_name}
                  </b>
                  <span className="mt-px block text-[11.5px] leading-snug text-ink-2">
                    {entry.description ||
                      (entry.fields.length
                        ? `${entry.fields.length} fields to start from`
                        : "Describe it yourself")}
                  </span>
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="mb-3.5 grid gap-3.5 sm:grid-cols-2">
        <div>
          <FieldLabel htmlFor="di-create-name">Collection name</FieldLabel>
          <FieldHelp className="mt-0 mb-1.5">
            Filled in from the kind you picked — change it to anything you like.
          </FieldHelp>
          <Input
            id="di-create-name"
            data-demo="collection-name"
            value={name}
            onChange={(event) => onNameChange(event.target.value)}
            placeholder="e.g. Candidate Resumes"
          />
        </div>
        <div>
          <FieldLabel required htmlFor="di-create-about">
            What are these documents about?
          </FieldLabel>
          <FieldHelp className="mt-0 mb-1.5">
            One line is enough — it helps us read them the way you would.
          </FieldHelp>
          <Textarea
            id="di-create-about"
            data-demo="create-about"
            value={about}
            onChange={(event) => onAboutChange(event.target.value)}
            className="min-h-10"
          />
        </div>
      </div>

      <div>
        <FieldLabel htmlFor="di-create-wanted">
          What do you want pulled out of them?
        </FieldLabel>
        <FieldHelp className="mt-0 mb-1.5">
          Filled in from the kind you picked — edit it, or write your own, one
          detail per line. These are the instructions we follow on every
          document, and you can come back to them on the next two steps.
        </FieldHelp>
        <Textarea
          id="di-create-wanted"
          data-demo="create-wanted"
          value={wanted}
          onChange={(event) => onWantedChange(event.target.value)}
          className="min-h-31"
          placeholder={
            "e.g.\nthe person's name\nhow many years they have worked\nevery skill they list\nfor each past job, the company, title, dates and what they did"
          }
        />
      </div>
    </>
  );
}
