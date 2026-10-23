"use client";

import Icon from "@/components/Icon";
import { Btn, IconBtn, LinkBtn, Select } from "@/components/ui";
import { cn } from "@/platform/utils";

import type { SortRule, TableColumn } from "./types";

/**
 * Multi-rule sort — the Data screen's, now the only one.
 *
 * The old Knowledge list had a single-column sort of its own on its header, so
 * the same rows could be ordered two ways depending on which screen you were
 * looking at them from. There is one sort now: rule order is precedence, and a
 * header click is its single-column shorthand.
 */
export default function SortPanel({
  columns,
  sorts,
  onChange,
  onAdd,
  onDone,
}: {
  columns: TableColumn[];
  sorts: SortRule[];
  onChange: (next: SortRule[]) => void;
  onAdd: () => void;
  onDone: () => void;
}) {
  const update = (index: number, patch: Partial<SortRule>) =>
    onChange(
      sorts.map((rule, position) =>
        position === index ? { ...rule, ...patch } : rule,
      ),
    );

  return (
    <div className="mb-3 rounded-di-lg border border-line bg-surface p-3 shadow-card">
      {sorts.length === 0 ? (
        <p className="mb-2 text-[12.5px] text-ink-2">
          Nothing is sorted. Add a rule, or click any column header.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {sorts.map((rule, index) => (
            <div
              key={`${rule.field}-${index}`}
              className="flex flex-wrap items-center gap-2"
            >
              <span className="w-10 shrink-0 text-[11.5px] text-ink-3 uppercase">
                {index === 0 ? "Sort by" : "Then"}
              </span>
              <Select
                aria-label="Column to sort by"
                value={rule.field}
                onChange={(event) =>
                  update(index, { field: event.target.value })
                }
                className="min-w-40 flex-1"
              >
                {columns.map((column) => (
                  <option key={column.key} value={column.key}>
                    {column.label}
                  </option>
                ))}
              </Select>
              <div
                className="inline-flex overflow-hidden rounded-di-md border border-line-strong"
                role="group"
                aria-label="Direction"
              >
                {(
                  [
                    { dir: 1 as const, label: "Ascending" },
                    { dir: -1 as const, label: "Descending" },
                  ] satisfies Array<{ dir: 1 | -1; label: string }>
                ).map((option) => (
                  <button
                    key={option.dir}
                    type="button"
                    aria-pressed={rule.dir === option.dir}
                    onClick={() => update(index, { dir: option.dir })}
                    className={cn(
                      "cursor-pointer px-2.5 py-1.5 text-[12.5px] font-semibold",
                      rule.dir === option.dir
                        ? "bg-accent-soft text-accent-softtext"
                        : "bg-surface text-ink-2 hover:bg-sunken",
                    )}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <IconBtn
                label="Remove this sort"
                onClick={() =>
                  onChange(sorts.filter((_, position) => position !== index))
                }
              >
                <Icon name="x" className="size-3.5" />
              </IconBtn>
            </div>
          ))}
        </div>
      )}

      <div className="mt-2.5 flex items-center justify-between border-t border-line-soft pt-2.5">
        <LinkBtn onClick={onAdd}>+ Add sort</LinkBtn>
        <div className="flex items-center gap-3.5">
          {sorts.length > 0 && (
            <LinkBtn className="text-ink-2" onClick={() => onChange([])}>
              Clear all
            </LinkBtn>
          )}
          <Btn variant="primary" size="sm" onClick={onDone}>
            Done
          </Btn>
        </div>
      </div>
    </div>
  );
}
