"use client";

import { useMemo, useState } from "react";

import { FAILED } from "@/components/conversations/constants";
import Icon from "@/components/Icon";
import {
  cellText,
  formatDate,
  isEmptyValue,
} from "@/components/knowledge/table/format";
import type {
  DocumentRow,
  TableColumn,
} from "@/components/knowledge/table/types";
import {
  Btn,
  Chip,
  EmptyNote,
  IconBtn,
  Pill,
  StatusPill,
} from "@/components/ui";
import type { DocumentSection, EnrichmentField } from "@/data/documentDetail";
import { cn } from "@/platform/utils";

import {
  groupFields,
  matchesQuery,
  type FieldEntry,
  type FieldGroup,
} from "./fieldGroups";
import JsonTree from "./JsonTree";
import { locateValue, type TextMatch } from "./locate";
import { rowObject } from "./rowJson";

/**
 * Every value this collection asked for, and what came back for this document.
 *
 * The order is the reader's, not the schema's: how the extraction went, then
 * the summary if there is one, then the fields — required first, because an
 * empty required field is a document worth looking at and an empty optional one
 * usually is not — and the document's own facts last.
 *
 * A field with no value says **Not found** rather than being left blank. A
 * blank cell is indistinguishable from a field the collection never asked for,
 * and that difference is the whole reason to open this screen.
 *
 * The list is the schema this document was *processed under*, not today's. A
 * field added to the collection afterwards was never looked for here, so it is
 * left out and named once in the status band — listing it as "Not found" makes
 * every older document look like an extraction that failed.
 *
 * Sixty-one fields is the normal case here, not the extreme one, so the list is
 * built to be scanned rather than read: one ruled card, values aligned in a
 * single column with the type held out at the margin, repeated objects folded
 * under one heading, and a search box and an empty-field switch to cut the list
 * down to the part being checked.
 *
 * A value that appears in the extracted text carries a "Show in text" control,
 * which points the viewer beside this list at it — the check this screen is for,
 * without reading the document top to bottom to find the line.
 */
export default function DocumentFields({
  row,
  fieldColumns,
  schema,
  sections,
  failReason,
  onLocate,
  onCopyJson,
}: {
  row: DocumentRow;
  fieldColumns: TableColumn[];
  /** The collection's fields, when they are known — required-ness comes from here. */
  schema: EnrichmentField[];
  /** The extracted text, which a value is looked up in. */
  sections: DocumentSection[];
  failReason: string | null;
  onLocate: (match: TextMatch, label: string) => void;
  onCopyJson: () => void;
}) {
  const [query, setQuery] = useState("");
  const [hideEmpty, setHideEmpty] = useState(false);

  const { extractable, addedLater } = useMemo(
    () => ({
      extractable: fieldColumns.filter(
        (column) => !row.notExtracted.has(column.key),
      ),
      addedLater: fieldColumns.filter((column) =>
        row.notExtracted.has(column.key),
      ),
    }),
    [fieldColumns, row.notExtracted],
  );

  const json = useMemo(() => rowObject(row), [row]);

  const filled = row.filled;
  const total = extractable.length;
  const status = String(row.document.status ?? "").toLowerCase();
  const failed = FAILED.has(status) || Boolean(failReason);

  // The summary is shown as prose above the list rather than as one more row:
  // it is the only field that is a paragraph, and in a two-column list it
  // pushes every other value off the screen. Having been shown, it is then
  // taken out of the list — the same value twice on one screen reads as two
  // different fields that happen to agree.
  const summaryColumn = extractable.find(
    (column) => column.key.toLowerCase() === "summary",
  );
  const summary = summaryColumn ? row.values[summaryColumn.key] : null;
  const showSummary = !isEmptyValue(summary);
  /** The summary's key once it has been shown as prose, so the list drops it. */
  const summaryKey = showSummary ? (summaryColumn?.key ?? null) : null;
  const listed = summaryKey
    ? extractable.filter((column) => column.key !== summaryKey)
    : extractable;

  const emptyCount = listed.filter((column) =>
    isEmptyValue(row.values[column.key]),
  ).length;

  // Grouping is the columns' shape and the filter is the reader's, so it is
  // computed from the columns and the schema alone — rebuilding a sixty-row
  // tree on every keystroke of a search that only hides rows is wasted work.
  const { requiredGroups, optionalGroups } = useMemo(() => {
    const requiredNames = new Set(
      schema.filter((field) => field.required).map((field) => field.name),
    );
    const listedColumns = summaryKey
      ? extractable.filter((column) => column.key !== summaryKey)
      : extractable;
    return {
      requiredGroups: groupFields(
        listedColumns.filter((column) => requiredNames.has(column.key)),
      ),
      optionalGroups: groupFields(
        listedColumns.filter((column) => !requiredNames.has(column.key)),
      ),
    };
  }, [extractable, schema, summaryKey]);

  const keep = (group: FieldGroup, entry: FieldEntry) => {
    const value = row.values[entry.column.key];
    if (hideEmpty && isEmptyValue(value)) return false;
    return matchesQuery(entry, group.label, value, query);
  };

  const narrow = (groups: FieldGroup[]) =>
    groups
      .map((group) => ({
        ...group,
        entries: group.entries.filter((entry) => keep(group, entry)),
      }))
      .filter((group) => group.entries.length > 0);

  const shownRequired = narrow(requiredGroups);
  const shownOptional = narrow(optionalGroups);
  const countEntries = (groups: FieldGroup[]) =>
    groups.reduce((count, group) => count + group.entries.length, 0);
  const shownCount = countEntries(shownRequired) + countEntries(shownOptional);
  const filtering = Boolean(query) || hideEmpty;

  /**
   * The type most of these fields are read as — named once in the caption, and
   * left off the rows that have it.
   *
   * Most collections are text from top to bottom, and a column of sixty
   * identical `Text` labels is a column of noise beside the values it was meant
   * to qualify. What is worth marking is the row that is *not* text.
   */
  const commonType = useMemo(() => {
    const counts = new Map<string, number>();
    for (const group of [...requiredGroups, ...optionalGroups])
      for (const entry of group.entries) {
        const label =
          schema.find((field) => field.name === entry.column.key)?.typeLabel ??
          entry.column.type;
        counts.set(label, (counts.get(label) ?? 0) + 1);
      }
    let winner: string | null = null;
    let best = 0;
    for (const [label, count] of counts)
      if (count > best) [winner, best] = [label, count];
    // Two of three fields sharing a type makes it the rule; a bare plurality
    // among five types is not, and hiding it would just lose the label.
    return best * 3 >= (shownCount || 1) * 2 ? winner : null;
  }, [requiredGroups, optionalGroups, schema, shownCount]);

  return (
    // A container, not a viewport, decides this panel's layout: it is half a
    // wide screen and the whole of a narrow one, and a `md:` rule would widen
    // the rows on a laptop exactly where there is least room for them.
    <div
      data-demo="document-fields"
      className="@container min-w-0 overflow-auto px-5 py-4"
    >
      <div
        className={cn(
          "mb-5 rounded-di-md border px-3.5 py-3",
          failed
            ? "border-danger-dot bg-danger-bg"
            : filled < total
              ? "border-warn-dot bg-warn-bg"
              : "border-line bg-sunken",
        )}
      >
        <div className="flex flex-wrap items-center gap-2.5">
          <StatusPill status={row.document.status} />
          <span className="text-[11.5px] text-ink-2">
            {/* The table already formats these two; reading them off the row
                rather than off the document is what keeps this band and that
                row saying the same thing. */}
            {String(row.values.addedvia ?? "File")}
            {row.values.created
              ? ` · added ${formatDate(row.values.created)}`
              : ""}
          </span>
          <span className="flex-1" />
          {total > 0 && (
            <Pill tone={filled === total ? "success" : "warn"}>
              <span className="tnum">
                {filled} of {total} fields filled
              </span>
            </Pill>
          )}
        </div>
        {failed ? (
          <p className="m-0 mt-2 text-[12.5px] leading-relaxed text-ink">
            {failReason ??
              "We could not read this document, so no field has a value."}
          </p>
        ) : filled < total ? (
          <p className="m-0 mt-2 text-[12.5px] leading-relaxed text-ink">
            {total - filled}{" "}
            {total - filled === 1 ? "field came" : "fields came"} back with no
            value. They are marked below.
          </p>
        ) : null}
        {addedLater.length > 0 && (
          <p className="m-0 mt-2 text-[12.5px] leading-relaxed text-ink-2">
            {addedLater.length}{" "}
            {addedLater.length === 1 ? "field was" : "fields were"} added to
            this collection after this document was processed, so{" "}
            {addedLater.length === 1 ? "it was" : "they were"} never extracted
            from it and {addedLater.length === 1 ? "is" : "are"} not listed:{" "}
            {addedLater.map((column) => column.label).join(", ")}.
          </p>
        )}
      </div>

      {showSummary && (
        <>
          <SectionHeading>Summary</SectionHeading>
          {/* Ruled down the accent edge: it is the one paragraph on a screen of
              values, and a plain grey box reads as one more field. */}
          <p className="m-0 mb-1 rounded-di-md border-y border-r border-l-[3px] border-line border-l-accent bg-sunken px-3.5 py-3 text-[13px] leading-[1.65] text-ink">
            {String(summary)}
          </p>
        </>
      )}

      {total > 0 && (
        <>
          <SectionHeading
            count={filtering ? `${shownCount} of ${total}` : `${total} fields`}
          >
            Extracted fields
          </SectionHeading>
          <p className="m-0 mb-2 text-[12.5px] leading-relaxed text-ink-2">
            {addedLater.length > 0
              ? "Every field this document was processed against, and what came back."
              : "Every field this collection asks for, and what came back for this document."}
            {showSummary ? " Summary is the one shown above." : ""}
            {commonType
              ? ` Values are read as ${commonType} unless the row says otherwise.`
              : ""}
          </p>

          {/* Sticky, because the point of a search box over a list this long is
              to retype the query once the first guess turns out to be the
              wrong word — and by then the box has scrolled away. */}
          <div className="sticky top-0 z-10 -mx-5 mb-2.5 flex flex-wrap items-center gap-2 border-b border-line-soft bg-surface px-5 pt-1 pb-2.5">
            <label className="relative min-w-[150px] flex-1">
              <span className="sr-only">Find a field</span>
              <Icon
                name="search"
                className="pointer-events-none absolute top-1/2 left-3 size-3.5 -translate-y-1/2 text-ink-3"
              />
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Find a field or a value"
                className="w-full rounded-full border border-line-strong bg-sunken py-1.5 pr-3 pl-9 text-[12.5px] text-ink outline-none placeholder:text-ink-3 focus-visible:border-accent focus-visible:bg-surface"
              />
            </label>
            {emptyCount > 0 && (
              <button
                type="button"
                aria-pressed={hideEmpty}
                onClick={() => setHideEmpty((previous) => !previous)}
                className={cn(
                  "inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px] font-semibold whitespace-nowrap",
                  hideEmpty
                    ? "border-accent bg-accent-soft text-accent-softtext"
                    : "border-line-strong bg-sunken text-ink-2 hover:text-ink",
                )}
              >
                <Icon
                  name={hideEmpty ? "eye" : "eyeOff"}
                  className="size-3.5"
                />
                {hideEmpty ? "Empty hidden" : `Hide ${emptyCount} empty`}
              </button>
            )}
          </div>

          {shownCount === 0 ? (
            <EmptyNote className="mt-1">
              No field matches “{query}”
              {hideEmpty ? " among the fields that have a value" : ""}.
            </EmptyNote>
          ) : (
            // One ruled card rather than sixty free-standing rows: the card's
            // edge is what tells the eye where a value's line ends, which is
            // the whole difficulty of reading a two-column list this long.
            <div className="overflow-hidden rounded-di-md border border-line bg-surface divide-y divide-line-soft">
              {shownRequired.length > 0 && (
                <FieldSection
                  label="Required"
                  count={countEntries(shownRequired)}
                  groups={shownRequired}
                  row={row}
                  schema={schema}
                  sections={sections}
                  onLocate={onLocate}
                  commonType={commonType}
                  required
                />
              )}
              {shownOptional.length > 0 && (
                <FieldSection
                  label={shownRequired.length > 0 ? "Optional" : null}
                  count={countEntries(shownOptional)}
                  groups={shownOptional}
                  row={row}
                  schema={schema}
                  sections={sections}
                  onLocate={onLocate}
                  commonType={commonType}
                />
              )}
            </div>
          )}
        </>
      )}

      {/* The same payload the header's JSON button copies, readable in place —
          checking what an integration will receive should not mean pasting it
          somewhere else first. Unfiltered by the search above: it is the
          whole row or it is not the JSON of the row. */}
      <SectionHeading>JSON</SectionHeading>
      <div className="overflow-hidden rounded-di-md border border-line bg-sunken">
        <div className="flex items-center gap-2 border-b border-line-soft px-3.5 py-1.5">
          <span className="flex-1 text-[12px] text-ink-2">
            Every extracted value for this document, as JSON.
          </span>
          <Btn
            size="sm"
            variant="ghost"
            title="Copy every field of this row as JSON"
            onClick={onCopyJson}
            startIcon={<Icon name="copy" className="size-3.5" />}
          >
            Copy
          </Btn>
        </div>
        <div
          data-demo="document-json"
          role="region"
          aria-label="Extracted values as JSON"
          tabIndex={0}
          className="max-h-[420px] overflow-auto py-3 pr-3.5 pl-7.5"
        >
          <JsonTree value={json} />
        </div>
      </div>

      <SectionHeading>Document details</SectionHeading>
      <div className="overflow-hidden rounded-di-md border border-line bg-surface divide-y divide-line-soft">
        <Detail label="Name">{String(row.values.file ?? "Untitled")}</Detail>
        <Detail label="Added via">
          <Pill tone="neutral">{String(row.values.addedvia ?? "File")}</Pill>
        </Detail>
        <Detail label="Status">
          <StatusPill status={row.document.status} />
        </Detail>
        <Detail label="Created">
          {/* Formatted the way the table's own Created column formats it — two
              screens printing one timestamp two ways is how a reader ends up
              wondering whether they are looking at the same document. */}
          <span className="tnum">{formatDate(row.values.created) || "—"}</span>
        </Detail>
        <Detail label="Last updated">
          <span className="tnum">{formatDate(row.values.updated) || "—"}</span>
        </Detail>
        <Detail label="Object ID">
          <span className="font-mono text-[12px] break-all">
            {row.objectId}
          </span>
        </Detail>
      </div>
    </div>
  );
}

function SectionHeading({
  count,
  children,
}: {
  count?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mt-6 mb-1.5 flex items-center gap-2 first:mt-0">
      <b className="text-[14px] tracking-[-0.01em] text-ink">{children}</b>
      {count && <Pill tone="count">{count}</Pill>}
    </div>
  );
}

/** The tinted strip that opens Required or Optional inside the card. */
function BandLabel({ label, count }: { label: string; count: number }) {
  return (
    <div className="flex items-center gap-2 bg-sunken px-3.5 py-1.5">
      <span className="text-[10.5px] font-bold tracking-[0.07em] text-ink-3 uppercase">
        {label}
      </span>
      <span className="tnum text-[10.5px] text-ink-3">{count}</span>
    </div>
  );
}

/**
 * Required or Optional: its strip, then its fields, run by run.
 *
 * Everything is returned as siblings of the card rather than wrapped per group
 * so that the card's `divide-y` draws exactly one hairline between each pair of
 * rows — nesting each group in its own box put two lines at every seam.
 */
function FieldSection({
  label,
  count,
  groups,
  row,
  schema,
  sections,
  onLocate,
  commonType,
  required,
}: {
  label: string | null;
  count: number;
  groups: FieldGroup[];
  row: DocumentRow;
  schema: EnrichmentField[];
  sections: DocumentSection[];
  onLocate: (match: TextMatch, label: string) => void;
  /** The type the caption already names, left off the rows that have it. */
  commonType: string | null;
  required?: boolean;
}) {
  return (
    <>
      {label && <BandLabel label={label} count={count} />}
      {groups.map((group, index) => (
        <FieldGroupRows
          key={
            group.label ? group.key : (group.entries[0]?.column.key ?? index)
          }
          group={group}
          row={row}
          schema={schema}
          sections={sections}
          onLocate={onLocate}
          commonType={commonType}
          required={required}
        />
      ))}
    </>
  );
}

function FieldGroupRows({
  group,
  row,
  schema,
  sections,
  onLocate,
  commonType,
  required,
}: {
  group: FieldGroup;
  row: DocumentRow;
  schema: EnrichmentField[];
  sections: DocumentSection[];
  onLocate: (match: TextMatch, label: string) => void;
  commonType: string | null;
  required?: boolean;
}) {
  const rows = group.entries.map((entry) => (
    <FieldRow
      key={entry.column.key}
      entry={entry}
      value={row.values[entry.column.key]}
      schema={schema}
      sections={sections}
      onLocate={onLocate}
      commonType={commonType}
      required={required}
    />
  ));

  if (!group.label) return <>{rows}</>;

  // The rows of one repeated object are headed together: the heading carries
  // the part of the name they share, so `Education #1 Degree` and `Education #1
  // Institution` can each be read as the one word they add.
  return (
    <>
      <div className="flex items-center gap-2.5 px-3.5 pt-2.5 pb-1">
        <span className="text-[11px] font-bold tracking-[0.02em] text-ink-2">
          {group.label}
        </span>
        <span className="h-px flex-1 bg-line-soft" />
      </div>
      {rows}
    </>
  );
}

/**
 * One field: its label, what came back, and the type it is read as.
 *
 * Three columns once the panel is wide enough to hold them — label, value, and
 * the type out at the right margin. Inline, the type was a pill per row that
 * the eye had to step over to reach the next value; at the margin the values
 * form a single column to read down and the type is still there for the row
 * being looked at. Under 460px the row stacks instead: label and type on one
 * line, the value under them with the full width, because three columns in a
 * 380px pane is four words per line and a torn address.
 */
function FieldRow({
  entry,
  value,
  schema,
  sections,
  onLocate,
  commonType,
  required,
}: {
  entry: FieldEntry;
  value: unknown;
  schema: EnrichmentField[];
  sections: DocumentSection[];
  onLocate: (match: TextMatch, label: string) => void;
  commonType: string | null;
  required?: boolean;
}) {
  const { column } = entry;
  const empty = isEmptyValue(value);
  const typeLabel =
    schema.find((field) => field.name === column.key)?.typeLabel ?? column.type;
  const match = useMemo(
    () => (empty ? null : locateValue(sections, column, value)),
    [empty, sections, column, value],
  );

  return (
    <div
      className={cn(
        "grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 px-3.5 py-2 text-[13px] hover:bg-sunken",
        "@[460px]:grid-cols-[minmax(120px,176px)_minmax(0,1fr)_auto] @[460px]:items-baseline @[460px]:gap-y-0",
      )}
    >
      <span
        className={cn(
          "col-start-1 row-start-1 text-[12px] font-medium",
          empty ? "text-ink-3" : "text-ink-2",
        )}
      >
        {entry.label}
      </span>
      <span
        className={cn(
          "col-span-2 col-start-1 row-start-2 flex min-w-0 flex-wrap items-center gap-1.5 text-[13.5px] leading-[1.5]",
          "@[460px]:col-span-1 @[460px]:col-start-2 @[460px]:row-start-1",
        )}
      >
        {empty ? (
          // A required field with nothing in it is the reason to open this
          // screen; an optional one is the ordinary case, and thirteen amber
          // pills down one side of the panel bury the values next to them.
          required ? (
            <Pill tone="warn">Not found</Pill>
          ) : (
            <span className="text-[12.5px] text-ink-3 italic">Not found</span>
          )
        ) : Array.isArray(value) ? (
          value.map((item, index) => (
            <Chip key={`${String(item)}-${index}`}>{String(item)}</Chip>
          ))
        ) : (
          <span
            className={cn(
              "break-words text-ink",
              column.key === "email" && "font-mono text-[12.5px]",
            )}
          >
            {cellText(column, value)}
          </span>
        )}
        {match && (
          <IconBtn
            label={`Show ${entry.label} in the extracted text`}
            // One anchor per field — `field-locate-fuel-rate-per-mile` — so a
            // demo can point at a particular value rather than the first one.
            data-demo={`field-locate-${column.key.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}
            onClick={() => onLocate(match, entry.label)}
            className="size-6 border-transparent bg-transparent text-ink-3 hover:border-line"
          >
            <Icon name="search" className="size-3" />
          </IconBtn>
        )}
      </span>
      {typeLabel !== commonType && (
        <span
          title={`${column.label} is read as ${typeLabel}`}
          className={cn(
            "col-start-2 row-start-1 justify-self-end font-mono text-[10px] whitespace-nowrap text-ink-3",
            "@[460px]:col-start-3 @[460px]:row-start-1",
          )}
        >
          {typeLabel}
        </span>
      )}
    </div>
  );
}

function Detail({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-y-0.5 px-3.5 py-2 text-[13px] @[460px]:grid-cols-[minmax(120px,176px)_minmax(0,1fr)] @[460px]:items-baseline @[460px]:gap-x-3">
      <span className="text-[12px] font-medium text-ink-2">{label}</span>
      <span className="min-w-0 text-[13.5px] text-ink">{children}</span>
    </div>
  );
}
