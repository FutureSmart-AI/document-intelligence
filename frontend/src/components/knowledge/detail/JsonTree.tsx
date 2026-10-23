"use client";

import { useState } from "react";

import Icon from "@/components/Icon";
import { cn } from "@/platform/utils";

import { JSON_INDENT_PX } from "./constants";

/**
 * JSON drawn as a tree: keys, strings, numbers and yes/no values each in their
 * own colour, objects and lists folding under a caret, and long strings
 * wrapping under their own key instead of running off the right edge.
 *
 * The reference was the agent UI's raw-document JSON. That
 * viewer is built on lucide and the slate palette, so it would not follow this
 * product's tokens or its dark theme — this draws the same thing in them. What
 * gets copied is still the plain `JSON.stringify` text, so the colours never
 * reach a clipboard.
 */
export default function JsonTree({ value }: { value: unknown }) {
  return (
    <div className="font-mono text-[12px] leading-[1.6]">
      <Node label="the whole row" value={value} depth={0} last />
    </div>
  );
}

function Node({
  name,
  label,
  value,
  depth,
  last,
}: {
  /** The key printed before the value; list items have none. */
  name?: string;
  /** What the fold control is called — the key, or "line_items #2". */
  label: string;
  value: unknown;
  depth: number;
  last: boolean;
}) {
  // Open down to a table row's own fields: the row, the table, one line item.
  const [open, setOpen] = useState(depth < 3);
  const comma = last ? null : <Punct>,</Punct>;
  const indent = { paddingLeft: depth * JSON_INDENT_PX };

  const isList = Array.isArray(value);
  const isObject = !isList && value !== null && typeof value === "object";
  if (!isList && !isObject)
    return (
      <div style={indent} className="flex min-w-0 items-baseline">
        {name !== undefined && <Key name={name} />}
        <span className="min-w-0 break-words whitespace-pre-wrap">
          <Primitive value={value} />
          {comma}
        </span>
      </div>
    );

  const entries: [string | undefined, unknown][] = isList
    ? (value as unknown[]).map((item) => [undefined, item])
    : Object.entries(value as Record<string, unknown>);
  const [opener, closer] = isList ? ["[", "]"] : ["{", "}"];

  if (!entries.length)
    return (
      <div style={indent}>
        {name !== undefined && <Key name={name} />}
        <Punct>
          {opener}
          {closer}
        </Punct>
        {comma}
      </div>
    );

  return (
    <div>
      <div style={indent} className="flex items-baseline">
        <button
          type="button"
          aria-expanded={open}
          aria-label={`${open ? "Fold" : "Unfold"} ${label}`}
          onClick={() => setOpen((previous) => !previous)}
          className="-ml-4 inline-flex size-4 shrink-0 cursor-pointer items-center justify-center self-center rounded-di-sm text-ink-3 hover:text-ink"
        >
          <Icon
            name="caretRight"
            className={cn(
              "size-3 transition-transform motion-reduce:transition-none",
              open && "rotate-90",
            )}
          />
        </button>
        {name !== undefined && <Key name={name} />}
        <Punct>{opener}</Punct>
        {!open && (
          <>
            <span className="mx-1 font-sans text-[11px] text-ink-3">
              {entries.length} {entries.length === 1 ? "item" : "items"}
            </span>
            <Punct>{closer}</Punct>
            {comma}
          </>
        )}
      </div>
      {open && (
        <>
          {entries.map(([key, item], index) => (
            <Node
              key={key ?? index}
              name={key}
              label={key ?? `${label} #${index + 1}`}
              value={item}
              depth={depth + 1}
              last={index === entries.length - 1}
            />
          ))}
          <div style={indent}>
            <Punct>{closer}</Punct>
            {comma}
          </div>
        </>
      )}
    </div>
  );
}

function Key({ name }: { name: string }) {
  return (
    <span className="shrink-0 whitespace-pre">
      <span className="text-accent">&quot;{name}&quot;</span>
      <Punct>: </Punct>
    </span>
  );
}

function Punct({ children }: { children: React.ReactNode }) {
  return <span className="text-ink-3">{children}</span>;
}

function Primitive({ value }: { value: unknown }) {
  if (value === null || value === undefined)
    return <span className="text-ink-3">null</span>;
  if (typeof value === "boolean")
    return <span className="text-warn-text">{String(value)}</span>;
  if (typeof value === "number")
    return <span className="text-success-text">{String(value)}</span>;
  return (
    <span className="text-danger-text">{JSON.stringify(String(value))}</span>
  );
}
