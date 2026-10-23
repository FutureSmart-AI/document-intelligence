"use client";

import Icon from "@/components/Icon";
import { Chip, Pill, StatusPill } from "@/components/ui";
import {
  parsedFileName,
  resolveDocumentLinks,
} from "@/platform/components/rag/dashboard/knowledge/list-view/documentLinks";
import type { KnowledgeItem } from "@/platform/hooks/useKnowledgeData";
import { cn } from "@/platform/utils";
import { downloadExternalFile } from "@/platform/utils/downloadHelper";

import {
  ACTIONS_WIDTH,
  COLUMN_GROUPS,
  COLUMN_WIDTH,
  MAX_CELL_CHIPS,
  statusBucket,
} from "./constants";
import { cellText, isEmptyValue } from "./format";
import { sortRank } from "./selectRows";
import type {
  ColumnGroupKey,
  DocumentRow,
  SortRule,
  TableColumn,
} from "./types";

function SortIndicator({ sorts, field }: { sorts: SortRule[]; field: string }) {
  const rank = sortRank(sorts, field);
  if (rank < 0)
    return (
      <span aria-hidden="true" className="ml-1 text-[10px] text-ink-3">
        ↕
      </span>
    );
  return (
    <span className="ml-1 text-[10px] text-accent">
      {sorts[rank].dir === 1 ? "↑" : "↓"}
      {/* A multi-rule sort has to say which rule this column is, or the header
          claims two columns are each "the" sort. */}
      {sorts.length > 1 && <span className="tnum">{rank + 1}</span>}
    </span>
  );
}

function Cell({
  column,
  row,
  fieldCount,
  dense,
}: {
  column: TableColumn;
  row: DocumentRow;
  fieldCount: number;
  /**
   * Rows kept to one line each.
   *
   * A list of skills is what makes a row three lines tall, and one such cell
   * sets the height of every cell beside it — so on a dense table the chips
   * stay on one line and the rest go to the counter, which already carries the
   * whole list in its tooltip.
   */
  dense?: boolean;
}) {
  const value = row.values[column.key];

  if (column.key === "file") {
    return (
      <>
        <div
          className="max-w-70 truncate font-semibold text-ink"
          title={cellText(column, value)}
        >
          {cellText(column, value)}
        </div>
        <div className="mt-0.5 truncate font-mono text-[10.5px] text-ink-3">
          {row.objectId}
        </div>
      </>
    );
  }

  if (column.key === "addedvia")
    return <Pill tone="neutral">{cellText(column, value)}</Pill>;

  if (column.key === "status")
    return (
      <span title={`Reported as “${row.document.status}”`}>
        <StatusPill status={row.document.status} />
      </span>
    );

  if (column.key === "filled") {
    const { filled, asked } = row;
    const tone = filled === 0 ? "danger" : filled < asked ? "warn" : "success";
    // The denominator is the fields this document was processed against, so
    // 9/9 means the extraction found everything it was asked for — a field
    // added to the collection since is not a gap in this document.
    const later = fieldCount - asked;
    return (
      <Pill tone={tone} className="tnum">
        <span
          title={
            `${filled} of ${asked} extracted fields have a value` +
            (later > 0
              ? ` · ${later} ${later === 1 ? "field was" : "fields were"} added after this document was processed`
              : "")
          }
        >
          {filled}/{asked}
        </span>
      </Pill>
    );
  }

  // Not "Not found": this document was processed before the field existed, so
  // nothing was ever looked for. Saying "Not found" reads as a failed
  // extraction on every older document in the collection.
  if (row.notExtracted.has(column.key))
    return (
      <span
        className="text-[11.5px] text-ink-3"
        title="Not extracted — this field was added after this document was processed"
      >
        <span aria-hidden="true">—</span>
        <span className="sr-only">Not extracted</span>
      </span>
    );

  if (isEmptyValue(value))
    return (
      <span
        className="text-[11.5px] text-ink-3 italic"
        title="This field has no value in this document"
      >
        Not found
      </span>
    );

  if (Array.isArray(value)) {
    const chips = value.slice(0, dense ? 2 : MAX_CELL_CHIPS);
    return (
      <span
        className={cn(
          "flex items-center gap-1",
          dense ? "flex-nowrap overflow-hidden" : "flex-wrap",
        )}
      >
        {chips.map((entry, index) => (
          <Chip
            key={`${String(entry)}-${index}`}
            tone="muted"
            className={dense ? "max-w-32 truncate" : undefined}
          >
            {String(entry)}
          </Chip>
        ))}
        {value.length > chips.length && (
          <span
            className="tnum text-[11px] text-ink-3"
            title={value.map((entry) => String(entry)).join(", ")}
          >
            +{value.length - chips.length}
          </span>
        )}
      </span>
    );
  }

  const text = cellText(column, value);
  return (
    <span
      className={cn(
        "block max-w-70 truncate",
        column.vtype === "num" || column.vtype === "date" ? "tnum" : undefined,
      )}
      title={text}
    >
      {text}
    </span>
  );
}

/**
 * One grid, three column groups.
 *
 * The top header row names the groups and carries the eye that hides one; the
 * second row is the columns themselves, each sortable, each printing the type
 * the service reports. The name column and both header rows stay stuck, because
 * a table 17 columns wide is read by scrolling sideways and a value with no
 * visible column heading and no visible document name says nothing.
 */
export default function KnowledgeGrid({
  columns,
  rows,
  fieldCount,
  sorts,
  compact,
  fullScreen,
  activeObjectId,
  emptyNote,
  onHeaderSort,
  onToggleGroup,
  onOpenRow,
  onDelete,
}: {
  /** The visible columns, in order. */
  columns: TableColumn[];
  rows: DocumentRow[];
  fieldCount: number;
  sorts: SortRule[];
  compact: boolean;
  /** The table has the page: the scroller takes what the viewport has left. */
  fullScreen?: boolean;
  activeObjectId?: string | null;
  emptyNote: string;
  onHeaderSort: (field: string) => void;
  onToggleGroup: (group: ColumnGroupKey) => void;
  onOpenRow: (row: DocumentRow) => void;
  onDelete: (document: KnowledgeItem) => void;
}) {
  const cellPadding = compact ? "px-3 py-1.75 text-[12.5px]" : "px-3 py-2.5";

  /**
   * The first row that has actually been read, for the demo anchor below.
   *
   * `-1` when none has: a collection whose every document failed has nothing
   * worth opening, and the anchor is then simply absent rather than pointing at
   * a screen with no values on it.
   */
  const firstReadIndex = rows.findIndex(
    (row) => statusBucket(row.document.status) === "completed",
  );

  /**
   * The group the row-actions cell rides with.
   *
   * It has to be the **last** group with a column on screen, because that cell
   * is the last one in every row. Giving the extra colspan to the first group
   * instead pushes every heading one column to the right of the columns it
   * names — "Status details" starting over Created rather than over Status.
   */
  const lastGroup = COLUMN_GROUPS.filter((group) =>
    columns.some((column) => column.group === group.key),
  ).at(-1)?.key;

  return (
    // The scroller, not the card: the card is the shell around this and the
    // footer. A bounded height is what makes both header rows stick — with the
    // page doing the scrolling instead, `position: sticky` has nothing to stick
    // inside and the column names walk off the top of a long collection.
    <div
      data-demo="knowledge-grid"
      className={cn(
        "overflow-auto",
        // 56vh leaves room for everything the page keeps above and below the
        // table. Full screen keeps nothing but the toolbar and the footer, so
        // the scroller takes the rest of the viewport rather than staying half
        // of it — which is what "the table has the whole page" has to mean.
        fullScreen ? "max-h-[calc(100vh-15.5rem)]" : "max-h-[56vh]",
      )}
    >
      {/* A wide collection is read by scrolling sideways, not by squeezing
          seventeen columns into the viewport: at `w-full` alone every cell got
          ~70px, so a chip list or a role title wrapped onto three lines and no
          two rows were the same height. Past four columns the table claims a
          readable width and the container scrolls. */}
      <table
        className="w-full border-collapse text-[13px]"
        style={
          columns.length > 4
            ? { minWidth: columns.length * COLUMN_WIDTH + ACTIONS_WIDTH }
            : undefined
        }
      >
        <thead>
          <tr>
            {COLUMN_GROUPS.map((group) => {
              const inGroup = columns.filter(
                (column) => column.group === group.key,
              );
              if (!inGroup.length) return null;
              const span = inGroup.length + (group.key === lastGroup ? 1 : 0);
              const hideLabel = `Hide the ${group.label.toLowerCase()} columns`;
              return (
                <th
                  key={group.key}
                  colSpan={span}
                  className={cn(
                    // Sticky top, but deliberately not sticky left: this cell
                    // spans its whole group, so pinning it would park a
                    // three-column-wide heading over the columns to its right.
                    "sticky top-0 z-2 h-7.5 border-b border-line bg-sunken px-3 text-left",
                    "text-[11px] tracking-[0.04em] text-ink-2 uppercase",
                  )}
                >
                  <span className="mr-1.75">{group.label}</span>
                  {/* How many of this group's columns are on screen. The
                      denominator lives on the toolbar's Columns badge, which
                      counts the whole table. */}
                  <span className="tnum rounded-full bg-surface px-1.5 py-px text-[10px] text-ink-2">
                    {inGroup.length}
                  </span>
                  <button
                    type="button"
                    disabled={group.locked}
                    onClick={() => onToggleGroup(group.key)}
                    title={group.locked ? group.note : hideLabel}
                    aria-label={group.locked ? group.note : hideLabel}
                    className={cn(
                      "ml-1.5 inline-flex size-5 items-center justify-center rounded-[4px] align-middle text-ink-3",
                      group.locked
                        ? "cursor-default opacity-40"
                        : "cursor-pointer hover:bg-raised hover:text-ink",
                    )}
                  >
                    <Icon name="eye" className="size-3.5" />
                  </button>
                </th>
              );
            })}
          </tr>
          <tr>
            {columns.map((column, index) => {
              const active = sortRank(sorts, column.key) > -1;
              return (
                <th
                  key={column.key}
                  aria-sort={
                    active
                      ? sorts[sortRank(sorts, column.key)].dir === 1
                        ? "ascending"
                        : "descending"
                      : "none"
                  }
                  className={cn(
                    "sticky top-7.5 z-1 border-b border-line px-3 py-1.75 text-left align-bottom",
                    "text-[11.5px] font-semibold whitespace-nowrap",
                    column.group === "status" ? "bg-sunken" : "bg-surface",
                    active ? "text-accent" : "text-ink-2",
                    index === 0 && "left-0 z-2 bg-sunken",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => onHeaderSort(column.key)}
                    title="Click to sort"
                    className="inline-flex cursor-pointer items-baseline gap-1 text-left"
                  >
                    {column.label}
                    <SortIndicator sorts={sorts} field={column.key} />
                  </button>
                  {/* The type the service reports, on its own line under the
                      column name. As a pill beside the label it competed with
                      the label for the eye and pushed the header wide; below
                      it, in mono, it reads as an annotation. */}
                  <span className="mt-0.5 block font-mono text-[9.5px] font-medium tracking-normal text-ink-3 normal-case">
                    {column.type}
                  </span>
                </th>
              );
            })}
            <th className="sticky top-7.5 z-1 border-b border-line bg-surface px-3 py-1.75" />
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length + 1}
                className="px-4 py-10 text-center text-[13px] text-ink-2"
              >
                {emptyNote}
              </td>
            </tr>
          ) : (
            rows.map((row, rowIndex) => {
              const links = resolveDocumentLinks(row.document);
              const isActive = activeObjectId === row.objectId;
              // Two anchors, and the second is not redundant: the leading row
              // is whatever the sort put there — often the one that failed to
              // read — and a demo opening *that* document shows a screen with
              // nothing in it. `knowledge-row-read` is the first row that has
              // actually been read, which is the one worth opening.
              const demoAnchor = rowIndex === 0 ? "knowledge-row" : undefined;
              const readAnchor =
                firstReadIndex === rowIndex ? "knowledge-row-read" : undefined;
              return (
                <tr
                  key={row.objectId}
                  data-demo={readAnchor ?? demoAnchor}
                  onClick={() => onOpenRow(row)}
                  className={cn(
                    "group cursor-pointer border-b border-line-soft last:border-b-0",
                    isActive ? "bg-accent-soft" : "hover:bg-sunken",
                  )}
                >
                  {columns.map((column, index) => (
                    <td
                      key={column.key}
                      className={cn(
                        cellPadding,
                        "align-top",
                        column.group === "status" && !isActive && "bg-sunken",
                        index === 0 &&
                          cn(
                            "sticky left-0 z-1",
                            isActive
                              ? "bg-accent-soft"
                              : "bg-surface group-hover:bg-sunken",
                          ),
                      )}
                    >
                      <Cell
                        column={column}
                        row={row}
                        fieldCount={fieldCount}
                        dense={compact}
                      />
                    </td>
                  ))}
                  <td className={cn(cellPadding, "align-top")}>
                    {/* Always on screen, not revealed by hover: download the
                        original, download the parsed text, delete. A row action
                        you cannot see is one you do not know exists, and on a
                        touch screen there is no hover to find it with. */}
                    <div
                      data-demo={
                        rowIndex === 0 ? "knowledge-row-actions" : undefined
                      }
                      className="flex items-center justify-end gap-0.5"
                      onClick={(event) => event.stopPropagation()}
                    >
                      {links.originalUrl && (
                        <RowAction
                          label="Download the original"
                          icon="dn"
                          onClick={() =>
                            void downloadExternalFile(
                              links.originalUrl as string,
                              row.document.file_name || "document",
                            )
                          }
                        />
                      )}
                      {links.parsedUrl && (
                        <RowAction
                          label="Download the parsed text"
                          icon="text"
                          onClick={() =>
                            void downloadExternalFile(
                              links.parsedUrl as string,
                              parsedFileName(
                                row.document.file_name || "document",
                              ),
                            )
                          }
                        />
                      )}
                      <RowAction
                        label="Delete this document"
                        icon="trash"
                        danger
                        onClick={() => onDelete(row.document)}
                      />
                    </div>
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

function RowAction({
  label,
  icon,
  danger,
  onClick,
}: {
  label: string;
  icon: "dn" | "text" | "trash";
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      className={cn(
        "inline-flex size-7 cursor-pointer items-center justify-center rounded-di-sm",
        danger
          ? "text-danger-text hover:bg-danger-bg"
          : "text-ink-2 hover:bg-raised hover:text-ink",
      )}
    >
      <Icon name={icon} className="size-3.75" />
    </button>
  );
}
