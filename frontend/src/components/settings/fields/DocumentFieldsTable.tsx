"use client";

import Icon from "@/components/Icon";
import { IconBtn, Pill } from "@/components/ui";
import { isProtectedField } from "@/platform/components/rag/dashboard/knowledge/enrichment-config-modal/constants";
import { typeInWords } from "@/platform/components/rag/dashboard/knowledge/enrichment-config-modal/utils";
import { cn } from "@/platform/utils";

export interface FieldRow {
  /** Where the row sits in the shared enrichment form's array. */
  index: number;
  id: number;
  field_name: string;
  description: string;
  field_type: string;
  field_type_label?: string | null;
  is_custom: boolean;
  is_required: boolean;
  is_active: boolean;
}

/**
 * What the collection pulls out of every document, one row each.
 *
 * Four columns and no more, as the beta design has it: the name, what it
 * captures, how it is read, and the rules that apply to it. The three columns
 * this replaced — a raw `field_type`, a status badge and a properties column —
 * said the same three things in the API's words and took twice the width.
 */
export default function DocumentFieldsTable({
  rows,
  busy,
  onEdit,
  onDelete,
  emptyNote,
}: {
  rows: FieldRow[];
  busy?: boolean;
  onEdit: (row: FieldRow) => void;
  onDelete: (row: FieldRow) => void;
  emptyNote: string;
}) {
  return (
    <div
      data-demo="fields-table"
      className="overflow-x-auto rounded-di-md border border-line bg-surface"
    >
      <table className="w-full min-w-150 table-fixed border-collapse">
        <thead>
          <tr>
            {[
              ["Field", "w-[19%]"],
              ["What it captures", "w-[40%]"],
              ["Type", "w-[13%]"],
              ["Rules", "w-[20%]"],
              ["", "w-[8%]"],
            ].map(([label, width]) => (
              <th
                key={label || "actions"}
                className={cn(
                  "border-b border-line bg-sunken px-3 py-2 text-left text-[10px] font-bold tracking-[0.06em] text-ink-3 uppercase",
                  width,
                )}
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((row) => {
              const protectedField = isProtectedField(row.field_name);
              return (
                <tr
                  key={row.field_name}
                  className={cn(
                    "border-b border-line-soft last:border-b-0",
                    !row.is_active && "opacity-60",
                  )}
                >
                  <td className="px-3 py-2.5 align-middle font-mono text-[12px] font-bold break-words text-ink">
                    {row.field_name}
                  </td>
                  {/* Three lines is enough to recognise a field by; the rest is
                      in the dialog, and left as a tooltip for a quick look. */}
                  <td
                    className="px-3 py-2.5 align-middle text-[12.5px] leading-snug text-ink-2"
                    title={row.description || undefined}
                  >
                    <span className="line-clamp-3">
                      {row.description || "—"}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 align-middle">
                    <Pill tone="neutral">{typeInWords(row)}</Pill>
                  </td>
                  <td className="px-3 py-2.5 align-middle">
                    <div className="flex flex-wrap items-center gap-1">
                      {!row.is_custom && <Pill tone="accent">Built-in</Pill>}
                      {row.is_required && <Pill tone="danger">Required</Pill>}
                      {protectedField && <Pill tone="neutral">Protected</Pill>}
                      {!row.is_active && <Pill tone="warn">Not in use</Pill>}
                      {row.is_custom &&
                        !row.is_required &&
                        !protectedField &&
                        row.is_active && <Pill tone="neutral">Optional</Pill>}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 align-middle">
                    <div className="flex items-center justify-end gap-1">
                      <IconBtn
                        data-demo="field-edit"
                        label={`Edit ${row.field_name}`}
                        disabled={busy}
                        onClick={() => onEdit(row)}
                      >
                        <Icon name="pencil" className="size-3.5" />
                      </IconBtn>
                      <IconBtn
                        // Only a row that can actually be removed carries the
                        // anchor, so a demo clicking the first one does not land
                        // on a protected field with the button disabled.
                        data-demo={protectedField ? undefined : "field-delete"}
                        label={
                          protectedField
                            ? `${row.field_name} is built in and cannot be removed`
                            : `Delete ${row.field_name}`
                        }
                        disabled={busy || protectedField}
                        className="text-danger-text hover:bg-danger-bg hover:text-danger-text"
                        onClick={() => onDelete(row)}
                      >
                        <Icon name="trash" className="size-3.5" />
                      </IconBtn>
                    </div>
                  </td>
                </tr>
              );
            })
          ) : (
            <tr>
              <td
                colSpan={5}
                className="px-3 py-6 text-center text-[12.5px] text-ink-2"
              >
                {emptyNote}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
