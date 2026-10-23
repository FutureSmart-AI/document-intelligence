"use client";

import type { ReactNode } from "react";

import type { FieldType } from "@/data/fieldCatalog";

/**
 * What each type means in the collection's own terms, and what it looks like
 * once it is in the table.
 *
 * The dialog never shows a raw `field_type`, but it does have to explain the
 * one that was chosen — "why did my field come back as a Date?" is the question
 * the pre-beta dialog left unanswered. The reasoning and the worked example are
 * keyed by the wire type rather than by the label because the label is the
 * service's free text: two deployments can word `List[str]` differently, and
 * both should still get the chips.
 */
interface TypeLore {
  why: string;
  /** One cell of the Data table, as this type renders in it. */
  sample: ReactNode;
  /** Keeps a list's "most values to keep" box. */
  list?: boolean;
  /** Opens the columns box — the same flag the catalogue calls `requires_schema`. */
  group?: boolean;
}

const val = (text: string) => (
  <span className="font-semibold text-ink">{text}</span>
);

const chip = (text: string, key: string) => (
  <span
    key={key}
    className="inline-flex items-center rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-semibold text-accent-softtext"
  >
    {text}
  </span>
);

const mini = (label: string, text: string) => (
  <span
    key={label}
    className="inline-flex items-center gap-1 rounded-di-sm border border-line bg-sunken px-1.5 py-0.5 text-[11px] text-ink-2"
  >
    <b className="font-mono text-[10.5px] text-ink-3">{label}</b> {text}
  </span>
);

const typeLore: Record<string, TypeLore> = {
  str: {
    why: "One value per document, written as text.",
    sample: val("Senior Data Engineer"),
  },
  "list[str]": {
    why: "You asked for more than one value, so we keep them all.",
    list: true,
    sample: (
      <span className="flex flex-wrap gap-1">
        {["Python", "Go", "Kubernetes"].map((entry) => chip(entry, entry))}
        {chip("+2", "more")}
      </span>
    ),
  },
  int: {
    why: "You asked for a count, so this keeps whole numbers and can be sorted, totalled and charted.",
    sample: val("7"),
  },
  float: {
    why: "You asked for an amount or a rate, so this keeps its decimals and can be sorted, totalled and charted.",
    sample: val("7.5"),
  },
  date: {
    why: "You asked for a point in time, so this can be sorted and filtered by date.",
    sample: val("28 Jul 2026"),
  },
  bool: {
    why: "You asked whether something is true, so the answer is one or the other.",
    sample: (
      <span className="inline-flex items-center rounded-full bg-success-bg px-2 py-0.5 text-[11px] font-semibold text-success-text">
        Yes
      </span>
    ),
  },
  dict: {
    why: "Several details that only make sense together, kept as one thing.",
    group: true,
    sample: (
      <span className="flex flex-wrap gap-1">
        {mini("company", "Zeta Systems")}
        {mini("role", "Backend Engineer")}
        {mini("from", "Mar 2021")}
      </span>
    ),
  },
  "list[dict]": {
    why: "One group of details per row — a job, a line item, a payment.",
    list: true,
    group: true,
    sample: (
      <span className="flex flex-col gap-0.5 text-[11.5px] text-ink-2">
        <span>
          <b className="text-ink">Zeta Systems</b> Backend Engineer · 2021–2024
        </span>
        <span>
          <b className="text-ink">Loop Labs</b> Engineer · 2019–2021
        </span>
      </span>
    ),
  },
};

/**
 * The lore for a wire type, or a sane account of one this file has never heard
 * of — the catalogue is the service's to grow, and an unknown type still has to
 * render something truthful rather than nothing.
 */
export const loreFor = (fieldType?: string | null): TypeLore =>
  typeLore[String(fieldType ?? "").toLowerCase()] ?? {
    why: "One value per document, as the service reads it.",
    sample: <span className="text-ink-3">a value from your documents</span>,
  };

/** Whether this type keeps more than one value — the "most values" box. */
export const isListType = (fieldType?: string | null): boolean =>
  Boolean(loreFor(fieldType).list);

/**
 * Whether this type is made of named parts.
 *
 * Read from the catalogue's `requires_schema` when the type is in it, because
 * that flag is the service's own answer; the lore is only the fallback.
 */
export const isGroupType = (
  types: FieldType[],
  fieldType?: string | null,
): boolean => {
  const entry = types.find(
    (candidate) =>
      candidate.field_type.toLowerCase() ===
      String(fieldType ?? "").toLowerCase(),
  );
  if (entry) return entry.requires_schema;
  return Boolean(loreFor(fieldType).group);
};

/**
 * A first guess at how a description should be read, from the words in it.
 *
 * This is a **preview**, never the answer: on create the service resolves the
 * type from the description itself and says why, and that reasoning is what the
 * save reports back. The guess exists so the dialog can show what a field is
 * about to look like in the table while it is being written, and so picking a
 * type by hand is a change from something rather than a blank.
 */
export const suggestWireType = (
  description: string,
  sampleValues: string[],
): string => {
  const text = ` ${String(description ?? "").toLowerCase()} `;
  const has = (...needles: string[]) =>
    needles.some((needle) => text.includes(needle));

  const guess = (): string => {
    if (
      has(
        "per row",
        "one row",
        "line item",
        "each job",
        "per job",
        "each entry",
        "table of",
        "one per invoice",
      )
    )
      return "List[dict]";
    if (
      has(
        "together",
        "as a group",
        "grouped",
        "nested",
        "sub-field",
        "subfield",
      )
    )
      return "dict";
    if (
      has(
        "every ",
        "all the",
        "each ",
        "list of",
        "any and all",
        "one or more",
        "comma-separated",
      )
    )
      return "List[str]";
    // Money and rates run BEFORE the count test: an `int` field keeps whole
    // numbers only, so "total fuel cost" read as a count stored 12.75 as 12
    // and a per-mile rate of 0.18 as 0. "rate" matches on a word boundary —
    // as a bare substring it fires on separate, corporate and accurate.
    if (
      /\brates?\b/.test(text) ||
      has(
        "amount",
        "price",
        "cost",
        "fee",
        "percentage",
        "percent",
        "average",
        "ratio",
        "decimal",
      )
    )
      return "float";
    if (
      has(
        "how many",
        "number of",
        "count of",
        "total",
        "years",
        "score",
        "quantity",
      )
    )
      return "int";
    // The yes-no test runs BEFORE the date test on purpose: "whether they can
    // start immediately" is a yes-no question that happens to carry a date word.
    if (
      has(
        "whether",
        "yes or no",
        "true or false",
        "does the",
        "is the document",
        "has the",
      )
    )
      return "bool";
    // "date" matches on a word boundary on purpose: as a bare substring it also
    // fires on candidate, update, mandate and validate, which typed a resume's
    // "the candidate's full name" as a Date.
    if (
      /\bdates?\b/.test(text) ||
      has(
        "when ",
        "deadline",
        "expiry",
        "expires",
        "start",
        "issued",
        "signed on",
        "due",
      )
    )
      return "date";
    return "str";
  };

  const wire = guess();
  // The accepted values are the second half of what the user told us: three
  // degrees are a fixed list of text, two numbers are a number, true/false is a
  // yes-no. They only refine a guess of plain text — they never override a
  // description that asked for a table.
  if (wire === "str" && sampleValues.length) {
    const allNumbers = sampleValues.every(
      (value) =>
        value !== "" && !Number.isNaN(Number(value.replace(/[,%]/g, ""))),
    );
    const yesNo =
      sampleValues.length <= 2 &&
      sampleValues.every((value) => /^(yes|no|true|false)$/i.test(value));
    if (yesNo) return "bool";
    if (allNumbers)
      return sampleValues.some((value) => value.includes("."))
        ? "float"
        : "int";
  }
  return wire;
};
