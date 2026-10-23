"use client";

import { useState } from "react";

import Icon from "@/components/Icon";
import { IconBtn, Pill } from "@/components/ui";
import {
  columnTypes,
  distinctTypes,
  labelFor,
  needsSchema,
  type FieldType,
} from "@/data/fieldCatalog";
import { cn } from "@/platform/utils";

import { TEXT_TYPE } from "./constants";
import {
  blankDraft,
  isUnfinished,
  slugify,
  validateDrafts,
  type FieldDraft,
} from "./fieldDraft";

/**
 * The fields a collection is about to be created with, one row each.
 *
 * One row rather than one card per field on purpose: as cards — a name, a type
 * pill and a description box each — five fields filled most of a screen and
 * read as a wall of identical boxes. Same controls, one line high, so a whole
 * set is scannable at a glance.
 *
 * Two things the board does that a plain list would not:
 *
 * - **The type dropdown is the service's catalogue**, not a constant. A type
 *   this app has never heard of still appears, with the service's own words for
 *   it, and the row sends back the same `field_type` it was given.
 * - **A type whose catalogue entry says `requires_schema` opens its columns**,
 *   because a table with no columns is a field the service will accept and
 *   nothing will ever fill. Branching on the flag rather than on a hardcoded
 *   pair of type names is what stops this going stale the next time the
 *   catalogue grows.
 *
 * Every row on the board is a field the collection will be created with, and
 * every row can be renamed, retyped, re-described or removed. The service's
 * optional defaults are not among them: they are not shown here and not sent,
 * so the board is the whole list rather than the list plus whatever the service
 * would have added on its own.
 */
export default function FieldBoard({
  drafts,
  onChange,
  types,
  disabled,
}: {
  drafts: FieldDraft[];
  onChange: (next: FieldDraft[]) => void;
  types: FieldType[];
  disabled?: boolean;
}) {
  const requiresSchema = (fieldType: string) => needsSchema(types, fieldType);
  const problems = validateDrafts(drafts, requiresSchema);
  const columns = columnTypes(types);

  /**
   * Which tables have their columns open.
   *
   * Every table's columns used to be open on every row at once, and a board of
   * thirteen fields where two of them were tables read as four boards stacked
   * on each other — you could not see the field list for the columns. A row is
   * one line until you ask it for more, and it asks for itself when something
   * is wrong with it (below) so nothing hides behind a summary.
   */
  const [openColumns, setOpenColumns] = useState<Set<string>>(new Set());
  const toggleColumns = (id: string) =>
    setOpenColumns((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const update = (id: string, patch: Partial<FieldDraft>) =>
    onChange(
      drafts.map((draft) => (draft.id === id ? { ...draft, ...patch } : draft)),
    );

  /** Picking a type is also what opens or closes its columns. */
  const retype = (draft: FieldDraft, fieldType: string) => {
    if (!requiresSchema(fieldType)) {
      const { schema: _dropped, ...rest } = draft;
      onChange(
        drafts.map((entry) =>
          entry.id === draft.id ? { ...rest, fieldType } : entry,
        ),
      );
      return;
    }
    // Asking for a table and being shown a summary of the columns it has not
    // got yet would be a riddle: this is the one moment they are the subject.
    setOpenColumns((current) => new Set(current).add(draft.id));
    update(draft.id, {
      fieldType,
      schema: draft.schema ?? { "": columns[0]?.field_type ?? "str" },
    });
  };

  /**
   * What a new row is typed as: the catalogue's text entry, by its own label,
   * falling back to the wire name the service has always used for it.
   */
  const textType =
    types.find((entry) => entry.field_type === TEXT_TYPE)?.field_type ??
    columns[0]?.field_type ??
    TEXT_TYPE;

  const columnCount = (draft: FieldDraft) =>
    Object.keys(draft.schema ?? {}).length;

  /**
   * A row shows its columns when asked — and asks for itself when it has a
   * problem or no columns at all, so nothing that blocks the step can hide
   * behind a summary line.
   */
  const columnsOpen = (draft: FieldDraft, problem?: string) =>
    openColumns.has(draft.id) || Boolean(problem) || columnCount(draft) === 0;

  const setColumns = (draft: FieldDraft, next: Record<string, string>) =>
    update(draft.id, { schema: next });

  const renameColumn = (draft: FieldDraft, from: string, to: string) => {
    // Rebuilt in order rather than patched, so renaming a column does not send
    // it to the bottom of the table while it is being typed.
    const next: Record<string, string> = {};
    for (const [name, type] of Object.entries(draft.schema ?? {}))
      next[name === from ? to : name] = type;
    setColumns(draft, next);
  };

  return (
    <div
      data-demo="field-board"
      className="overflow-x-auto rounded-di-md border border-line bg-surface"
    >
      {drafts.length ? (
        <table className="w-full min-w-130 table-fixed border-collapse">
          <thead>
            <tr>
              {[
                ["Field name", "w-[27%]"],
                ["What it captures", ""],
                ["Read as", "w-[22%]"],
                // Wide enough that the remove button is not pressed against
                // the edge of the board — it sits in its own column, centred.
                ["", "w-12"],
              ].map(([label, width]) => (
                <th
                  key={label || "remove"}
                  className={cn(
                    "border-b border-line bg-sunken px-2.5 py-1.75 text-left text-[10px] font-bold tracking-[0.06em] text-ink-3 uppercase",
                    width,
                  )}
                >
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {drafts.map((draft) => {
              const problem = problems[draft.id];
              // Two different things wear the same message slot: a row still
              // being written, which is simply left out, and a row that would
              // break the create, which has to be fixed. Only the second one
              // gets the alarm colour — a blank row somebody just added is not
              // an error to be told off for.
              const waiting = isUnfinished(draft);
              return (
                <tr
                  key={draft.id}
                  data-demo="field-row"
                  className={cn(
                    // The board reads as a panel of rows rather than a sheet of
                    // white: the artifact tints every row, and the controls on
                    // it come up white on hover instead of the other way round.
                    "group border-b border-line-soft bg-sunken last:border-b-0",
                    problem && (waiting ? "bg-warn-bg" : "bg-danger-bg"),
                  )}
                >
                  <td className="px-2 py-1.5 align-middle">
                    <div className="flex items-center gap-1.5">
                      <input
                        value={draft.name}
                        disabled={disabled}
                        aria-label="Field name"
                        // Only the add button makes a `custom` draft, so these
                        // anchors name the row you just added — the one a demo
                        // has to fill in before the step will let it continue.
                        data-demo={
                          draft.origin === "custom"
                            ? "field-custom-name"
                            : undefined
                        }
                        title="Rename this field"
                        onChange={(event) =>
                          update(draft.id, {
                            name: slugify(event.target.value, true),
                          })
                        }
                        // Tidy the edges only once they have finished, and in
                        // place — re-rendering the board on a keystroke is what
                        // eats the caret mid-edit.
                        onBlur={() =>
                          update(draft.id, { name: slugify(draft.name) })
                        }
                        className="w-full min-w-0 rounded-di-sm border border-transparent bg-transparent px-1.75 py-1.25 font-mono text-[12px] font-bold text-ink outline-none hover:border-line-strong hover:bg-surface focus:border-accent focus:bg-surface disabled:hover:border-transparent disabled:hover:bg-transparent"
                      />
                      {draft.origin === "suggested" && (
                        <Pill tone="accent">from your document</Pill>
                      )}
                    </div>
                  </td>

                  <td className="px-2 py-1.5 align-middle">
                    <textarea
                      rows={1}
                      value={draft.desc}
                      disabled={disabled}
                      aria-label={`What ${draft.name} should capture`}
                      data-demo={
                        draft.origin === "custom"
                          ? "field-custom-desc"
                          : undefined
                      }
                      placeholder="Describe it the way you would explain it to a new colleague."
                      onChange={(event) =>
                        update(draft.id, { desc: event.target.value })
                      }
                      className="h-[27px] w-full resize-none overflow-hidden rounded-di-sm border border-transparent bg-transparent px-1.75 py-1 text-[12.5px] leading-[17px] text-ink outline-none hover:border-line-strong hover:bg-surface focus:h-auto focus:min-h-14 focus:overflow-auto focus:border-accent focus:bg-surface"
                    />
                    {draft.why && (
                      <div className="hidden px-1.75 pt-0.5 pb-0.75 text-[10.5px] leading-snug text-ink-3 group-focus-within:block">
                        {draft.why}
                      </div>
                    )}
                    {problem && (
                      <div
                        className={cn(
                          "px-1.75 pt-0.5 pb-0.75 text-[11px] font-semibold",
                          waiting ? "text-warn-text" : "text-danger-text",
                        )}
                      >
                        {problem}
                      </div>
                    )}

                    {requiresSchema(draft.fieldType) &&
                      (columnsOpen(draft, problem) ? (
                        <ColumnBuilder
                          draft={draft}
                          columns={columns}
                          disabled={disabled}
                          onRename={(from, to) => renameColumn(draft, from, to)}
                          onRetype={(name, type) =>
                            setColumns(draft, {
                              ...(draft.schema ?? {}),
                              [name]: type,
                            })
                          }
                          onRemove={(name) => {
                            const next = { ...(draft.schema ?? {}) };
                            delete next[name];
                            setColumns(draft, next);
                          }}
                          onAdd={() =>
                            setColumns(draft, {
                              ...(draft.schema ?? {}),
                              "": columns[0]?.field_type ?? "str",
                            })
                          }
                          onClose={
                            problem || columnCount(draft) === 0
                              ? undefined
                              : () => toggleColumns(draft.id)
                          }
                        />
                      ) : (
                        <button
                          type="button"
                          disabled={disabled}
                          data-demo="field-columns-open"
                          title={`Show the columns of ${draft.name}`}
                          onClick={() => toggleColumns(draft.id)}
                          className="mt-0.5 ml-1.75 inline-flex cursor-pointer items-center gap-1 rounded-di-sm text-[11px] font-semibold text-accent hover:underline disabled:cursor-not-allowed"
                        >
                          <Icon name="caret" className="size-2.5" />
                          {columnCount(draft)} column
                          {columnCount(draft) === 1 ? "" : "s"}
                        </button>
                      ))}
                  </td>

                  <td className="px-2 py-1.5 align-middle">
                    <select
                      value={draft.fieldType}
                      data-demo={
                        draft.origin === "custom"
                          ? "field-custom-type"
                          : "field-type"
                      }
                      disabled={disabled || !types.length}
                      aria-label={`What ${draft.name} is read as`}
                      onChange={(event) => retype(draft, event.target.value)}
                      className="w-full cursor-pointer rounded-di-sm border border-transparent bg-transparent px-1.75 py-1.25 text-[12px] font-semibold text-ink-2 outline-none hover:border-line-strong hover:bg-surface focus:border-accent focus:bg-surface"
                    >
                      {distinctTypes(types).map((entry) => (
                        <option key={entry.field_type} value={entry.field_type}>
                          {entry.field_type_label}
                        </option>
                      ))}
                      {/* A type the catalogue no longer lists still has to be
                          selectable, or the row silently retypes itself. */}
                      {draft.fieldType &&
                        !types.some(
                          (entry) => entry.field_type === draft.fieldType,
                        ) && (
                          <option value={draft.fieldType}>
                            {labelFor(types, draft.fieldType)}
                          </option>
                        )}
                    </select>
                  </td>

                  <td className="px-1.5 py-1.5 text-center align-middle">
                    <IconBtn
                      label={`Do not pull out ${draft.name}`}
                      tooltip={false}
                      disabled={disabled}
                      onClick={() =>
                        onChange(
                          drafts.filter((entry) => entry.id !== draft.id),
                        )
                      }
                      className="size-[26px] text-ink-3 hover:border-danger-text hover:bg-danger-bg hover:text-danger-text"
                    >
                      <Icon name="x" className="size-3.5" />
                    </IconBtn>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <p className="px-3.5 py-4.5 text-center text-[12.5px] text-ink-2">
          No fields yet. Add one, or go back and describe what you want pulled
          out.
        </p>
      )}

      <button
        type="button"
        disabled={disabled}
        title="Add another field to pull out"
        data-demo="field-add"
        onClick={() => onChange([...drafts, blankDraft(drafts, textType)])}
        className="flex w-full cursor-pointer items-center gap-1.75 border-t border-line bg-sunken px-2.5 py-2 text-[12.5px] font-bold text-accent hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Icon name="plus" className="size-3.5" />
        Add a field
      </button>
    </div>
  );
}

/**
 * The columns of a table or a group of fields.
 *
 * Offered types are the catalogue minus the ones that need a schema of their
 * own: a table's columns cannot themselves be tables, so a `List[dict]` column
 * is not a choice the service would accept.
 */
function ColumnBuilder({
  draft,
  columns,
  disabled,
  onRename,
  onRetype,
  onRemove,
  onAdd,
  onClose,
}: {
  draft: FieldDraft;
  columns: FieldType[];
  disabled?: boolean;
  onRename: (from: string, to: string) => void;
  onRetype: (name: string, type: string) => void;
  onRemove: (name: string) => void;
  onAdd: () => void;
  /** Absent while the row still needs its columns — there is nothing to fold. */
  onClose?: () => void;
}) {
  const entries = Object.entries(draft.schema ?? {});

  return (
    <div
      data-demo="field-columns"
      className="mt-1.5 rounded-di-sm border border-line-soft bg-surface px-2 py-1.75"
    >
      <div className="mb-1 flex items-center gap-2">
        <span className="text-[10.5px] font-bold tracking-[0.05em] text-ink-3 uppercase">
          One row per entry — its columns
        </span>
        <div className="flex-1" />
        {onClose && (
          <button
            type="button"
            disabled={disabled}
            title={`Fold the columns of ${draft.name} away`}
            onClick={onClose}
            className="cursor-pointer text-[11px] font-semibold text-accent hover:underline disabled:cursor-not-allowed"
          >
            Done
          </button>
        )}
      </div>
      {entries.map(([name, type], index) => (
        <div key={index} className="mb-1 flex items-center gap-1.5 last:mb-0">
          <input
            value={name}
            disabled={disabled}
            aria-label={`Sub-field ${index + 1} of ${draft.name}`}
            // Anchors the first column still waiting for a name, so a demo
            // always points at the one holding the step up.
            data-demo={name ? undefined : "field-column-name"}
            placeholder="column_name"
            onChange={(event) =>
              onRename(name, slugify(event.target.value, true))
            }
            onBlur={() => onRename(name, slugify(name))}
            className="min-w-0 flex-1 rounded-di-sm border border-line bg-surface px-1.5 py-1 font-mono text-[11.5px] text-ink outline-none focus:border-accent"
          />
          <select
            value={type}
            disabled={disabled}
            aria-label={`What ${name || "this column"} is read as`}
            onChange={(event) => onRetype(name, event.target.value)}
            className="w-32 cursor-pointer rounded-di-sm border border-line bg-surface px-1.5 py-1 text-[11.5px] font-semibold text-ink-2 outline-none focus:border-accent"
          >
            {distinctTypes(columns).map((entry) => (
              <option key={entry.field_type} value={entry.field_type}>
                {entry.field_type_label}
              </option>
            ))}
          </select>
          <IconBtn
            label={`Remove the ${name || "unnamed"} column`}
            tooltip={false}
            disabled={disabled}
            onClick={() => onRemove(name)}
            className="border-transparent bg-transparent text-danger-text hover:bg-danger-bg"
          >
            <Icon name="x" className="size-3" />
          </IconBtn>
        </div>
      ))}
      <button
        type="button"
        disabled={disabled}
        onClick={onAdd}
        // Unconditional. It used to be set only on a table with no columns, so
        // that a demo pointed at the one blocking the step — but the service now
        // publishes its tables with their columns already filled in, and an
        // anchor that exists only in the state nothing reaches is an anchor
        // nothing can click.
        data-demo="field-columns-add"
        className="mt-0.5 cursor-pointer border-0 bg-transparent p-0 text-[11.5px] font-bold text-accent disabled:opacity-50"
      >
        + Add a column
      </button>
    </div>
  );
}
