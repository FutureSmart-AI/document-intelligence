"use client";

import { useMemo, useState } from "react";

import Icon from "@/components/Icon";
import { Btn, LinkBtn } from "@/components/ui";
import { cn } from "@/platform/utils";

import { COLUMN_GROUPS } from "./constants";
import type { TableColumn } from "./types";

/**
 * The fine axis: which individual columns are on screen.
 *
 * Grouped, with a select-all per group, because a wide collection can define
 * dozens of fields and a flat checklist of them is unreadable. Hiding a whole
 * group with the Show control never touches these boxes, so
 * All → Fields only → All puts back exactly the columns that were there.
 */
export default function ColumnsPanel({
  columns,
  hidden,
  onSetHidden,
  onSetMany,
  onDone,
}: {
  columns: TableColumn[];
  hidden: Record<string, boolean>;
  onSetHidden: (key: string, value: boolean) => void;
  onSetMany: (keys: string[], value: boolean) => void;
  onDone: () => void;
}) {
  const [search, setSearch] = useState("");

  const shownCount = useMemo(
    () =>
      columns.filter((column) => column.fixed || !hidden[column.key]).length,
    [columns, hidden],
  );

  const needle = search.trim().toLowerCase();
  const switchable = columns
    .filter((column) => !column.fixed)
    .map((column) => column.key);

  return (
    <div className="w-[min(30rem,calc(100vw-3rem))]">
      <div className="mb-2 flex items-center gap-1.5 rounded-di-md border border-line bg-sunken px-2 py-1.5 text-ink-3">
        <Icon name="search" className="size-3" />
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search columns…"
          aria-label="Search columns"
          className="w-full border-0 bg-transparent text-[12.5px] text-ink outline-none"
        />
      </div>

      <div className="mb-1 flex items-center justify-between text-[12px] text-ink-2">
        <span className="flex items-center gap-1.5">
          <LinkBtn
            className="text-[12px]"
            onClick={() => onSetMany(switchable, false)}
          >
            Select all
          </LinkBtn>
          <span aria-hidden="true">|</span>
          <LinkBtn
            className="text-[12px] text-ink-2"
            onClick={() => onSetMany(switchable, true)}
          >
            Unselect all
          </LinkBtn>
        </span>
        <span className="tnum">
          {shownCount}/{columns.length}
        </span>
      </div>

      <div className="max-h-72 overflow-auto">
        {COLUMN_GROUPS.map((group) => {
          const mine = columns.filter(
            (column) =>
              column.group === group.key &&
              (!needle || column.label.toLowerCase().includes(needle)),
          );
          if (!mine.length) return null;
          const mineSwitchable = mine
            .filter((column) => !column.fixed)
            .map((column) => column.key);
          return (
            <div key={group.key}>
              <div className="mt-2.5 mb-1 flex items-center gap-2 text-[11px] tracking-[0.04em] text-ink-2 uppercase">
                <b className="text-[11px]">{group.label}</b>
                <LinkBtn
                  className="text-[11.5px]"
                  onClick={() => onSetMany(mineSwitchable, false)}
                >
                  All
                </LinkBtn>
                <LinkBtn
                  className="text-[11.5px] text-ink-2"
                  onClick={() => onSetMany(mineSwitchable, true)}
                >
                  None
                </LinkBtn>
              </div>
              <div className="grid gap-x-3 gap-y-0.5 sm:grid-cols-2">
                {mine.map((column) => (
                  <label
                    key={column.key}
                    title={
                      column.fixed
                        ? "The name and how it was added always stay visible."
                        : undefined
                    }
                    className={cn(
                      "flex items-center gap-2 rounded-di-sm px-1.5 py-1 text-[12.5px] text-ink",
                      column.fixed
                        ? "cursor-default opacity-55"
                        : "cursor-pointer hover:bg-sunken",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={column.fixed || !hidden[column.key]}
                      disabled={column.fixed}
                      onChange={(event) =>
                        onSetHidden(column.key, !event.target.checked)
                      }
                      className="accent-accent"
                    />
                    {/* Label over type, the same annotation the grid header
                        prints, so a column is recognised by the same two lines
                        in both places. */}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{column.label}</span>
                      <span className="block font-mono text-[10px] text-ink-3">
                        {column.type}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-2.5 flex justify-end border-t border-line-soft pt-2.5">
        <Btn variant="primary" size="sm" onClick={onDone}>
          Done
        </Btn>
      </div>
    </div>
  );
}
