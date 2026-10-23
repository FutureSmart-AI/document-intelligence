"use client";

import Icon from "@/components/Icon";
import { Btn, IconBtn, Input, LinkBtn, Select } from "@/components/ui";

import {
  FILTER_OPS,
  VALUELESS_OPS,
  type FilterOp,
  type FilterRule,
  type TableColumn,
} from "./types";

/**
 * Filter rules, applied as they are typed.
 *
 * Live rather than draft-then-apply, because the facet band writes into this
 * same list: a value clicked in the band takes effect at once, so a rule typed
 * here has to as well or the screen would hold two filter states that look
 * identical and behave differently. A rule with an empty value matches
 * everything (see `matchesRule`), so adding a row never blanks the table
 * before it has been filled in.
 */
export default function FiltersPanel({
  columns,
  filters,
  onChange,
  onClear,
  onAdd,
  onDone,
}: {
  columns: TableColumn[];
  filters: FilterRule[];
  onChange: (next: FilterRule[]) => void;
  onClear: () => void;
  onAdd: () => void;
  onDone: () => void;
}) {
  const update = (id: string, patch: Partial<FilterRule>) =>
    onChange(
      filters.map((rule) => (rule.id === id ? { ...rule, ...patch } : rule)),
    );

  return (
    <div className="mb-3 rounded-di-lg border border-line bg-surface p-3 shadow-card">
      {filters.length === 0 ? (
        <p className="mb-2 text-[12.5px] text-ink-2">
          No rules yet. Add one to keep only the documents that match — or click
          a value in Explore, which writes a rule here.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {filters.map((rule, index) => {
            const needsValue = !VALUELESS_OPS.includes(rule.op);
            return (
              <div key={rule.id} className="flex flex-wrap items-center gap-2">
                <span className="w-10 shrink-0 text-[11.5px] text-ink-3 uppercase">
                  {index === 0 ? "Where" : "And"}
                </span>
                <Select
                  aria-label="Column to filter on"
                  value={rule.field}
                  onChange={(event) =>
                    update(rule.id, { field: event.target.value })
                  }
                  className="min-w-40 flex-1"
                >
                  {columns.map((column) => (
                    <option key={column.key} value={column.key}>
                      {column.label}
                    </option>
                  ))}
                </Select>
                <Select
                  aria-label="Condition"
                  value={rule.op}
                  onChange={(event) =>
                    update(rule.id, { op: event.target.value as FilterOp })
                  }
                  className="min-w-36"
                >
                  {FILTER_OPS.map((op) => (
                    <option key={op} value={op}>
                      {op}
                    </option>
                  ))}
                </Select>
                {rule.op === "Is any of" ? (
                  /* The set the facet band writes, shown as what it is: the
                     values it keeps, each removable, plus whether it also keeps
                     the documents that have none. A rule the band can write and
                     the panel cannot show would make them two filters. */
                  <span className="flex min-w-40 flex-1 flex-wrap items-center gap-1.5">
                    {(rule.values ?? []).map((value) => (
                      <span
                        key={value}
                        className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2 py-0.5 text-[11.5px] font-semibold text-accent-softtext"
                      >
                        {value}
                        <button
                          type="button"
                          aria-label={`Stop keeping ${value}`}
                          onClick={() =>
                            update(rule.id, {
                              values: (rule.values ?? []).filter(
                                (entry) => entry !== value,
                              ),
                            })
                          }
                          className="cursor-pointer text-ink-3 hover:text-danger-text"
                        >
                          <Icon name="x" className="size-2.5" />
                        </button>
                      </span>
                    ))}
                    {rule.includeEmpty && (
                      <span className="inline-flex items-center gap-1 rounded-full border border-line bg-sunken px-2 py-0.5 text-[11.5px] text-ink-2 italic">
                        (no value)
                        <button
                          type="button"
                          aria-label="Stop keeping the documents with no value"
                          onClick={() =>
                            update(rule.id, { includeEmpty: false })
                          }
                          className="cursor-pointer text-ink-3 not-italic hover:text-danger-text"
                        >
                          <Icon name="x" className="size-2.5" />
                        </button>
                      </span>
                    )}
                    {!(rule.values ?? []).length && !rule.includeEmpty && (
                      <span className="text-[12px] text-ink-3">
                        Nothing picked yet — click values in Group &amp; chart.
                      </span>
                    )}
                  </span>
                ) : needsValue ? (
                  <Input
                    aria-label="Value"
                    value={rule.value}
                    onChange={(event) =>
                      update(rule.id, { value: event.target.value })
                    }
                    placeholder="Value…"
                    className="min-w-40 flex-1"
                  />
                ) : (
                  <span className="min-w-40 flex-1 text-[12px] text-ink-3">
                    No value needed
                  </span>
                )}
                <IconBtn
                  label="Remove this rule"
                  onClick={() =>
                    onChange(filters.filter((other) => other.id !== rule.id))
                  }
                >
                  <Icon name="x" className="size-3.5" />
                </IconBtn>
              </div>
            );
          })}
        </div>
      )}

      <div className="mt-2.5 flex items-center justify-between border-t border-line-soft pt-2.5">
        <LinkBtn onClick={onAdd}>+ Add rule</LinkBtn>
        <div className="flex items-center gap-3.5">
          <LinkBtn className="text-ink-2" onClick={onClear}>
            Clear all
          </LinkBtn>
          <Btn variant="primary" size="sm" onClick={onDone}>
            Done
          </Btn>
        </div>
      </div>
    </div>
  );
}
