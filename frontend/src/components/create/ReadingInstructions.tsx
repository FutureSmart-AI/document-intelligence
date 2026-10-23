"use client";

import Icon from "@/components/Icon";
import { LinkBtn, Textarea } from "@/components/ui";
import { cn } from "@/platform/utils";

/**
 * How these documents should be read — one box, opened from two steps.
 *
 * The instructions are a property of the whole collection, not of the step you
 * happen to be on: the same prose that tells the schema call which fields to
 * propose is the prose that explains why a field keeps coming out empty on a
 * sample. So the field board (step two) and the sample (step three) open the
 * same box, against the same value, rather than each carrying a copy.
 *
 * Folded away by default. It is the thing you reach for when something is
 * wrong, and open by default it reads as another form to fill in before
 * anything will work — which it is not, because step one already collected it.
 *
 * Re-reading is an explicit button and not an on-blur effect: it spends a
 * metered `POST /v1/enrichment/generate` and rewrites the board underneath
 * someone, so it happens when they ask for it.
 */
export default function ReadingInstructions({
  value,
  onChange,
  open,
  onToggle,
  onReread,
  busy,
}: {
  value: string;
  onChange: (next: string) => void;
  open: boolean;
  onToggle: () => void;
  onReread: () => void;
  busy?: boolean;
}) {
  return (
    <div data-demo="reading-instructions" className="mt-3.5">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls="di-create-instructions-box"
        data-demo="reading-instructions-toggle"
        className="flex cursor-pointer items-center gap-1.5 border-0 bg-transparent p-0 text-[12.5px] font-bold text-accent"
      >
        How we should read these documents
        <Icon
          name="dn"
          className={cn("size-3 transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <div
          id="di-create-instructions-box"
          className="mt-2 rounded-di-md border border-line bg-sunken px-3 py-2.5"
        >
          <p className="m-0 mb-2 text-[11.5px] leading-snug text-ink-2">
            These are the instructions we follow on every document. Edit them if
            a field keeps coming out wrong, then run the sample again.
          </p>
          <Textarea
            aria-label="How we should read these documents"
            data-demo="reading-instructions-text"
            value={value}
            disabled={busy}
            onChange={(event) => onChange(event.target.value)}
            className="min-h-22 bg-surface"
          />
          <LinkBtn
            className="mt-2 text-[12.5px]"
            disabled={busy || !value.trim()}
            data-demo="reading-instructions-reread"
            onClick={onReread}
          >
            Re-read the fields from these instructions
          </LinkBtn>
        </div>
      )}
    </div>
  );
}
