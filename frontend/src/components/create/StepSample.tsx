"use client";

import { Fragment, useRef, type ReactNode } from "react";

import Icon from "@/components/Icon";
import { Btn, FieldHelp, IconBtn, LinkBtn, Pill } from "@/components/ui";
import { labelFor, type FieldType } from "@/data/fieldCatalog";
import type { SamplePreview, SuggestedField } from "@/data/schemaDraft";
import { readableSize } from "@/lib/fileSize";
import { cn } from "@/platform/utils";

import { WAITING_SHOWN } from "./constants";
import FieldBoard from "./FieldBoard";
import { isUnfinished, slugify, type FieldDraft } from "./fieldDraft";

/**
 * Step three: one of your own documents, run against the fields as they stand.
 *
 * This is a real run, not a rehearsal. `POST /v1/enrichment/preview` takes the
 * current field list and one document and answers with what it pulled out —
 * before any collection exists to hold either. So the question this step
 * answers is the one people actually have at this point: *do these fields work
 * on my documents?*
 *
 * ## Why the fields are on this step too
 *
 * The answer to that question is usually "nearly", and the fix is a reworded
 * field. Sending somebody back a step to make it and forward again to see the
 * effect is what made this loop feel heavy, so the board is here, editable,
 * beside what it produced, and **Run again** is in its own header. Read, check,
 * fix, read again — without leaving the step. It is the same `drafts` array
 * step two edits, so an edit made here is the edit that gets created.
 *
 * Three things are worth reading off the result, and each is shown as itself:
 *
 * - a **value** per field, which is the answer to the question;
 * - **not found**, which is a legitimate outcome and not a failure — the field
 *   is right, this document just does not carry it;
 * - **fields the document has that nobody asked for**, which is the thing prose
 *   alone cannot give you: someone asking for "the total" does not think to ask
 *   for the tax line beside it, and the document does. One click adds it.
 *
 * Nothing here is saved. Editing a field after a run marks the result stale
 * rather than hiding it: the old values stay on screen, dimmed, so the change
 * can be compared against what it replaced.
 */
export default function StepSample({
  drafts,
  onDraftsChange,
  types,
  instructions,
  notes,
  file,
  onPick,
  onClear,
  onRun,
  preview,
  runs,
  stale,
  running,
  failure,
  onAcceptSuggestion,
  onDismissSuggestion,
  acceptedNames,
  dismissedNames,
  busy,
}: {
  /** The same list step two edits — this board is the other host for it. */
  drafts: FieldDraft[];
  onDraftsChange: (next: FieldDraft[]) => void;
  types: FieldType[];
  /** The reading instructions box, folded open under the board. */
  instructions?: ReactNode;
  /** What the board has to say about itself — rejected fields, and the like. */
  notes?: ReactNode;
  file: File | null;
  onPick: (file: File) => void;
  /** Puts the document back, and the column with it — nothing was saved. */
  onClear: () => void;
  onRun: () => void;
  preview: SamplePreview | null;
  /** Which run is on screen, so a second one is visibly a second one. */
  runs: number;
  /** The fields have changed since that run, so the values are out of date. */
  stale?: boolean;
  running?: boolean;
  /** What went wrong, already turned into words the reader can act on. */
  failure?: string | null;
  onAcceptSuggestion: (field: SuggestedField) => void;
  onDismissSuggestion: (fieldName: string) => void;
  acceptedNames: Set<string>;
  dismissedNames: Set<string>;
  busy?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const kept = drafts.length;

  /**
   * Rows nobody has finished writing, which hold the run.
   *
   * They are left out of what a run is read with, so a run would quietly
   * answer for fewer fields than the board shows — and the field somebody was
   * in the middle of adding is exactly the one they are about to test. The
   * button says so instead of running behind their back.
   */
  const waiting = drafts.filter(isUnfinished);
  const waitingName = slugify(waiting[0]?.name ?? "");
  const blocked =
    waiting.length === 1
      ? waitingName
        ? `${waitingName} has no description yet — say what it captures, or remove the row.`
        : "A field with no name is on the board — name it, or remove the row."
      : waiting.length
        ? `${waiting.length} fields are unfinished — finish them, or remove the rows.`
        : null;

  /**
   * The one thing to press on this step, in the one place it belongs: under
   * the picker, above the fields it is about to fill in. "Run" the first time,
   * "Run again" once there is a result to compare against, and the primary
   * action whenever the values on screen are out of date with the list on the
   * left.
   */
  const runControl = (
    <div className="mb-2.25 flex flex-wrap items-center gap-2">
      <Btn
        size="sm"
        variant={!preview || stale ? "primary" : "default"}
        data-demo="sample-run"
        disabled={busy || running || !kept || !file || Boolean(blocked)}
        title={
          blocked
            ? blocked
            : file
              ? `Read ${file.name} with the fields as they are now`
              : "Choose a document first."
        }
        onClick={onRun}
      >
        {running ? "Running…" : preview ? "Run again" : "Run"}
      </Btn>
      <span className="text-[11.5px] leading-snug text-ink-2">
        {blocked
          ? blocked
          : !file
            ? "Choose a document and we will run your fields against it."
            : running
              ? `Reading ${file.name}…`
              : !preview
                ? `Runs your ${kept} field${kept === 1 ? "" : "s"} against ${file.name}. Nothing is saved.`
                : stale
                  ? "The fields changed — run it again to see what they read now."
                  : "Change anything on the left and run it again."}
      </span>
    </div>
  );

  return (
    <>
      <p
        data-demo="sample-step"
        className="mb-3.5 text-[12.5px] leading-relaxed text-ink-2"
      >
        Try the fields on <b>one</b> of your own documents, fix anything that
        comes out wrong right here, and run it again as often as you like.{" "}
        <b>Nothing is saved</b> until the last step — your other documents come
        after this.
      </p>

      {/* One file, deliberately. Handing over a folder to find out whether the
          fields are right is the burden this step exists to avoid. */}
      <input
        ref={input}
        type="file"
        data-demo="sample-input"
        className="hidden"
        onChange={(event) => {
          const picked = event.target.files?.[0];
          if (picked) onPick(picked);
          event.target.value = "";
        }}
      />

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <div>
          <ColumnHead step={1} title="Your fields" count={kept}>
            <span className="text-[12px] text-ink-2">
              Edit anything in place. You can change it again later in{" "}
              <b>Settings → Extracted fields</b>.
            </span>
          </ColumnHead>

          {preview && stale && (
            <div className="mb-2.25 flex items-center gap-2 rounded-di-md border border-warn-text bg-warn-bg px-2.75 py-2.25 text-[12px] leading-snug text-ink">
              <Icon
                name="info"
                className="size-3.75 flex-none text-warn-text"
              />
              <span>
                You changed the fields. <b>Run again</b> to see what they read
                out of this document now.
              </span>
            </div>
          )}

          <FieldBoard
            drafts={drafts}
            onChange={onDraftsChange}
            types={types}
            disabled={busy || running}
          />

          {notes}
          {instructions}
        </div>

        <div>
          <ColumnHead step={2} title="What we read out of it">
            {/* Only once there is a document to swap. Before that the dropzone
                below is the way in, and a second control saying the same thing
                a few pixels above it just splits the one obvious next move. */}
            {file && (
              <LinkBtn
                data-demo="sample-swap"
                title="Try these fields on a different document"
                onClick={() => input.current?.click()}
              >
                Try another document
              </LinkBtn>
            )}
          </ColumnHead>

          {file && (
            <div className="mb-2.25 flex items-center gap-2.25 rounded-di-md border border-line bg-surface px-2.75 py-2.25">
              <Icon name="file" className="size-4 flex-none text-ink-3" />
              <span className="min-w-0 flex-1 truncate text-[12.5px] font-bold text-ink">
                {file.name}
              </span>
              <span className="flex-none text-[11.5px] text-ink-3 tabular-nums">
                {readableSize(file.size)}
              </span>
              {/* The document is the one thing on this step that was chosen
                  rather than proposed, so taking it back has to be as easy as
                  choosing it — swapping it is not the only thing people want. */}
              <IconBtn
                label={`Remove ${file.name}`}
                tooltip={false}
                data-demo="sample-clear"
                disabled={busy || running}
                onClick={onClear}
                className="size-[26px] text-ink-3 hover:border-danger-text hover:bg-danger-bg hover:text-danger-text"
              >
                <Icon name="x" className="size-3.5" />
              </IconBtn>
            </div>
          )}

          {failure && (
            <p
              role="alert"
              className="mb-2.25 rounded-di-md border border-danger-text bg-danger-bg px-3 py-2 text-[12.5px] leading-relaxed text-danger-text"
            >
              {failure}
            </p>
          )}

          {preview ? (
            <>
              {runControl}
              <PreviewResult
                preview={preview}
                types={types}
                runs={runs}
                stale={stale}
                onAcceptSuggestion={onAcceptSuggestion}
                onDismissSuggestion={onDismissSuggestion}
                acceptedNames={acceptedNames}
                dismissedNames={dismissedNames}
                disabled={busy || running}
              />
            </>
          ) : (
            <Waiting
              drafts={drafts}
              types={types}
              running={running}
              busy={busy}
              hasFile={Boolean(file)}
              onChoose={() => input.current?.click()}
              runControl={runControl}
            />
          )}
        </div>
      </div>
    </>
  );
}

/** A numbered column header with room for that column's one control. */
function ColumnHead({
  step,
  title,
  count,
  children,
}: {
  step: number;
  title: string;
  count?: number;
  children?: ReactNode;
}) {
  return (
    <div className="mb-2.25 flex items-center gap-2">
      <span className="flex size-4.75 flex-none items-center justify-center rounded-full bg-accent text-[10.5px] font-extrabold text-accent-contrast">
        {step}
      </span>
      <b className="text-[12.5px] text-ink">{title}</b>
      {count !== undefined && <Pill tone="count">{count}</Pill>}
      <div className="flex-1" />
      {children}
    </div>
  );
}

/**
 * The results column before a run.
 *
 * It used to be one dropzone and a lot of nothing, which is what made the two
 * halves of the step look unrelated. Listing the fields that are waiting for a
 * value lets the column preview its own output: these names, filled in.
 */
function Waiting({
  drafts,
  types,
  running,
  busy,
  hasFile,
  onChoose,
  runControl,
}: {
  drafts: FieldDraft[];
  types: FieldType[];
  running?: boolean;
  busy?: boolean;
  hasFile: boolean;
  onChoose: () => void;
  /** Sits under the picker, where the offer of a sample document used to. */
  runControl: ReactNode;
}) {
  const shown = drafts.slice(0, WAITING_SHOWN);
  const rest = drafts.length - shown.length;

  return (
    <>
      {running ? (
        <div
          role="status"
          className="rounded-di-lg border-[1.5px] border-dashed border-line-strong bg-sunken px-4 py-7 text-center text-[12.5px] text-ink-2"
        >
          <b className="mb-0.75 block text-[13.5px] text-ink">
            Reading your document…
          </b>
          Running {drafts.length} field{drafts.length === 1 ? "" : "s"} against
          it.
        </div>
      ) : (
        !hasFile && (
          <button
            type="button"
            disabled={busy}
            onClick={onChoose}
            className="w-full cursor-pointer rounded-di-lg border-[1.5px] border-dashed border-line-strong bg-sunken px-4 py-7 text-center text-[12.5px] text-ink-2 hover:border-accent hover:bg-accent-soft disabled:cursor-not-allowed"
          >
            <Icon name="up" className="mx-auto mb-1.5 size-6.5 text-ink-3" />
            <b className="mb-0.75 block text-[13.5px] text-ink">
              Choose one document
            </b>
            PDF, DOCX, TXT or an image · run against your fields, not uploaded
          </button>
        )
      )}

      <div className="mt-2.25">{runControl}</div>

      {drafts.length > 0 && (
        <div
          data-demo="sample-waiting"
          className="mt-3 overflow-hidden rounded-di-md border border-line"
        >
          <div className="border-b border-line bg-sunken px-2.75 py-2 text-[11px] font-bold tracking-[0.05em] text-ink-3 uppercase">
            {drafts.length} field{drafts.length === 1 ? "" : "s"} waiting for a
            value
          </div>
          {shown.map((draft) => (
            <div
              key={draft.id}
              className="flex items-center gap-2.25 border-b border-line-soft px-2.75 py-1.75 last:border-b-0"
            >
              <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-ink-2">
                {draft.name || "unnamed_field"}
              </span>
              <span className="flex-none text-[11px] text-ink-3">
                {draft.fieldType
                  ? labelFor(types, draft.fieldType)
                  : "Worked out for you"}
              </span>
            </div>
          ))}
          {rest > 0 && (
            <div className="px-2.75 py-1.75 font-mono text-[11.5px] text-ink-3">
              and {rest} more
            </div>
          )}
        </div>
      )}
    </>
  );
}

/** What one document produced, field by field. */
function PreviewResult({
  preview,
  types,
  runs,
  stale,
  onAcceptSuggestion,
  onDismissSuggestion,
  acceptedNames,
  dismissedNames,
  disabled,
}: {
  preview: SamplePreview;
  types: FieldType[];
  runs: number;
  stale?: boolean;
  onAcceptSuggestion: (field: SuggestedField) => void;
  onDismissSuggestion: (fieldName: string) => void;
  acceptedNames: Set<string>;
  dismissedNames: Set<string>;
  disabled?: boolean;
}) {
  const missing = preview.fields_run.filter(
    (run) => run.status !== "extracted",
  ).length;
  const suggestions = preview.suggested_fields.filter(
    (field) =>
      !acceptedNames.has(field.field_name) &&
      !dismissedNames.has(field.field_name),
  );

  return (
    <div data-demo="sample-result">
      {/* Dimmed rather than dropped: what the old fields read is the thing the
          new ones are being compared against. */}
      <div
        className={cn(
          "overflow-hidden rounded-di-md border border-line",
          stale && "opacity-80",
        )}
      >
        {/* The values scroll inside their own box rather than pushing the page
            down: a table field can be ten rows of cards on its own, and a
            column that grows without limit leaves the field list it is meant
            to be read against far above the fold. */}
        <div
          data-demo="sample-values"
          className="max-h-[min(62vh,640px)] overflow-y-auto"
        >
          <div className="sticky top-0 z-1 flex items-center gap-2 border-b border-line bg-sunken px-3 py-2.25 text-[12px] font-bold text-ink">
            <Icon name="chk" className="size-3.5 text-success-text" />
            Run {runs} · {preview.fields_run.length} field
            {preview.fields_run.length === 1 ? "" : "s"}
            {preview.document.text_truncated && (
              <>
                <div className="flex-1" />
                <Pill tone="warn">First part only</Pill>
              </>
            )}
          </div>
          {preview.fields_run.map((run) => (
            <div
              key={run.field_name}
              className="flex gap-2.5 border-b border-line-soft px-3 py-2.25 text-[12.5px] last:border-b-0"
            >
              <span className="w-2/5 flex-none font-mono text-[11.5px] break-words text-ink-2">
                {run.field_name}
              </span>
              <span className="min-w-0 flex-1 leading-snug">
                {run.status === "extracted" ? (
                  <Value value={preview.extracted[run.field_name]} />
                ) : (
                  // A field the document does not carry is a correct answer, not
                  // an error: "not found beats a guess" is the service's own rule
                  // and this reads as neutral on purpose.
                  <span className="text-ink-3 italic">
                    Not on this document — the field is still fine.
                  </span>
                )}
              </span>
            </div>
          ))}
        </div>
      </div>

      <FieldHelp className="mt-2.25">
        {missing ? (
          // Said in the same voice as the line below it: one sentence, the
          // controls named in bold and nothing else, and the same reassurance
          // at the end — an empty field is a wording problem to work on here,
          // not a failure to be announced in bold.
          <>
            {missing} field{missing === 1 ? "" : "s"} came back empty. Reword{" "}
            {missing === 1 ? "it" : "them"} on the left, or open{" "}
            <b>How we should read these documents</b>, then <b>Run again</b> —
            nothing is saved until the last step.
          </>
        ) : (
          <>
            Every field came back with a value. Not right? Change it on the left
            and <b>Run again</b> — nothing is saved until the last step.
          </>
        )}
      </FieldHelp>

      {/* Said once the block empties, rather than the block simply vanishing:
          what happened to the fields this document offered is worth a line. */}
      {preview.suggested_fields.length > 0 && suggestions.length === 0 && (
        <FieldHelp className="mt-3">
          Everything else we spotted in this document has been dealt with — it
          is either on your field list or turned down.
        </FieldHelp>
      )}

      {suggestions.length > 0 && (
        <div
          data-demo="sample-suggestions"
          className="mt-3 rounded-di-md border border-accent bg-accent-soft px-3 py-2.5"
        >
          <b className="block text-[12.5px] text-ink">
            We also found these in your document
          </b>
          <p className="mt-0.75 mb-2.25 text-[11.5px] leading-snug text-ink-2">
            You did not ask for them, but this document has them. Add the ones
            you want — they behave exactly like the fields you wrote.
          </p>
          {suggestions.map((field) => (
            <div
              key={field.field_name}
              className="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1.5 rounded-di-sm border border-line bg-surface px-2.25 py-1.75 last:mb-0"
            >
              <span className="font-mono text-[12px] font-bold text-ink">
                {field.field_name}
              </span>
              {field.field_type && (
                <Pill tone="neutral">{labelFor(types, field.field_type)}</Pill>
              )}
              {/* `basis` rather than a bare `flex-1`: the reason is a sentence,
                  and left to share a row with two buttons it collapses into a
                  one-word-per-line column. It takes a whole line instead. */}
              <span className="min-w-0 flex-1 basis-50 text-[11px] leading-snug text-ink-2">
                {field.reason ?? "Found on this document."}
              </span>
              {/* Two buttons per row, several rows: the visible words stay
                  short, and the field they act on is in the accessible name
                  rather than only in the row they happen to sit in. */}
              <Btn
                size="sm"
                disabled={disabled}
                aria-label={`Add ${field.field_name}`}
                title={`Add ${field.field_name} to the fields we pull out`}
                onClick={() => onAcceptSuggestion(field)}
              >
                Add
              </Btn>
              <Btn
                size="sm"
                disabled={disabled}
                aria-label={`Not needed: ${field.field_name}`}
                title={`Do not pull out ${field.field_name}`}
                onClick={() => onDismissSuggestion(field.field_name)}
              >
                Not needed
              </Btn>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * One extracted value, printed for what it is.
 *
 * A table field comes back as rows of objects and a list as an array, and
 * `JSON.stringify` on either is the fastest way to make a correct answer look
 * like a bug. Joining an object's keys onto one line is only a little better:
 * `company: ABC · title: Software Developer · start_year: 2021 · …` is a
 * sentence nobody reads to the end of. So each shape gets the rendering it
 * wants — rows of objects become a small card per row with the keys as
 * labels, a list of plain values becomes chips, and one value stays one value.
 */
function Value({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "")
    return <span className="text-ink-3">—</span>;

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-ink-3">—</span>;

    // Rows of objects — one job, one degree, one line item. Each is its own
    // card, so where one ends and the next begins is visible at a glance.
    if (value.every(isRecord))
      return (
        <div className="flex flex-col gap-1.5">
          {value.map((entry, index) => (
            <Pairs key={index} entry={entry} boxed />
          ))}
        </div>
      );

    // A list of plain values — skills, tags, line items. Chips read as a set,
    // which is what this is; a column of bare words reads as a paragraph that
    // lost its punctuation.
    return (
      <ul className="m-0 flex list-none flex-wrap gap-1 p-0">
        {value.map((entry, index) => (
          <li
            key={index}
            className="rounded-full border border-line bg-sunken px-2 py-0.5 text-[11.5px] font-semibold text-ink"
          >
            {plain(entry)}
          </li>
        ))}
      </ul>
    );
  }

  if (isRecord(value)) return <Pairs entry={value} />;

  return <span className="font-semibold text-ink">{String(value)}</span>;
}

/** An object's keys down the left, its values down the right. */
function Pairs({
  entry,
  boxed,
}: {
  entry: Record<string, unknown>;
  boxed?: boolean;
}) {
  const pairs = Object.entries(entry);
  if (pairs.length === 0) return <span className="text-ink-3">—</span>;

  return (
    <dl
      className={cn(
        "m-0 grid grid-cols-[minmax(0,max-content)_minmax(0,1fr)] gap-x-2.5 gap-y-0.5",
        boxed && "rounded-di-md border border-line bg-sunken px-2.5 py-1.75",
      )}
    >
      {pairs.map(([key, sub]) => (
        <Fragment key={key}>
          <dt className="text-[11.5px] leading-snug text-ink-3">
            {humanise(key)}
          </dt>
          <dd className="m-0 text-[12.5px] leading-snug font-semibold text-ink">
            {plain(sub)}
          </dd>
        </Fragment>
      ))}
    </dl>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** `start_year` is a field name; "Start year" is what you call it out loud. */
function humanise(key: string) {
  const words = key.replace(/[_-]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : key;
}

/**
 * A value one level down, where a card is more structure than it is worth —
 * still never `[object Object]`.
 */
function plain(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value))
    return value.length ? value.map(plain).join(", ") : "—";
  if (isRecord(value))
    return Object.entries(value)
      .map(([key, sub]) => `${humanise(key)}: ${plain(sub)}`)
      .join(" · ");
  return String(value);
}
