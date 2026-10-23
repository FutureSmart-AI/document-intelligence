"use client";

import { useMemo, useState } from "react";

import Dropdown from "@/components/Dropdown";
import Icon from "@/components/Icon";
import { LinkBtn } from "@/components/ui";
import { cn } from "@/platform/utils";

import { MAX_DONUT_SLICES, MAX_FACET_CHIPS, VIZ_MODES } from "./constants";
import {
  coverageItems,
  facetableColumns,
  facetCounts,
  facetValueLabel,
  histogram,
  type FacetValue,
} from "./facets";
import type {
  DocumentRow,
  FilterRule,
  TableColumn,
  TableQuery,
  VizMode,
} from "./types";
import { Bars, Coverage, Distribution, Donut, VizNote } from "./Viz";

/**
 * The facet band — the Explore screen, folded in above the table.
 *
 * A band rather than a 320px side rail: the rail is what forced Explore to be a
 * screen of its own. Grouping by a column charts it and offers its values as
 * chips, and clicking one writes a filter rule the table and the export both
 * obey.
 */
export default function FacetBar({
  rows,
  viewRows,
  columns,
  visibleCols,
  query,
  field,
  onFieldChange,
  viz,
  onVizChange,
  onToggleValue,
  onClearFilters,
}: {
  /** Every document, so the counts describe the collection, not the page. */
  rows: DocumentRow[];
  /** The rows currently in view — what Distribution and Coverage measure. */
  viewRows: DocumentRow[];
  columns: TableColumn[];
  visibleCols: TableColumn[];
  query: TableQuery;
  field: string;
  onFieldChange: (key: string) => void;
  viz: VizMode;
  onVizChange: (mode: VizMode) => void;
  onToggleValue: (column: TableColumn, value: string, missing: boolean) => void;
  onClearFilters: () => void;
}) {
  const [valueSearch, setValueSearch] = useState("");

  const groupable = useMemo(() => facetableColumns(columns), [columns]);
  const column =
    groupable.find((candidate) => candidate.key === field) ?? groupable[0];

  const facets = useMemo(
    () =>
      column
        ? facetCounts(rows, columns, column.key, query)
        : { values: [], base: 0 },
    [rows, columns, column, query],
  );

  const activeRule = useMemo<FilterRule | undefined>(
    () => query.filters.find((rule) => rule.field === column?.key),
    [query.filters, column],
  );

  /**
   * Selected means "in this field's set" — several values can be, at once.
   *
   * `Equals` is still read here so a rule typed by hand in the Filters panel
   * lights its chip up too: the band and the panel are one filter, and a rule
   * the panel can express that the band cannot show would break that.
   */
  const isSelected = (facet: FacetValue) => {
    if (!activeRule) return false;
    if (facet.missing)
      return activeRule.op === "Is empty" || !!activeRule.includeEmpty;
    if (activeRule.op === "Is any of")
      return (activeRule.values ?? []).includes(facet.value);
    return activeRule.op === "Equals" && activeRule.value === facet.value;
  };

  const max = facets.values.length ? facets.values[0].count : 1;
  const numeric = column?.vtype === "num";
  // A distribution needs numbers; asking for one on a text column falls back to
  // bars rather than drawing an empty axis.
  const effectiveViz: VizMode = viz === "dist" && !numeric ? "bars" : viz;

  const items = facets.values.map((facet) => ({
    label: facetValueLabel(facet) || "(no value)",
    count: facet.count,
  }));

  const filteredValues = useMemo(() => {
    const needle = valueSearch.trim().toLowerCase();
    if (!needle) return facets.values;
    return facets.values.filter((facet) =>
      facetValueLabel(facet).toLowerCase().includes(needle),
    );
  }, [facets.values, valueSearch]);

  if (!column) return null;

  return (
    <div
      data-demo="knowledge-facet-bar"
      className="mb-3.5 rounded-di-md border border-line bg-surface p-3.5"
    >
      <div className="relative flex flex-wrap items-center gap-2.5">
        <Dropdown
          panelClassName="w-58 max-h-80 overflow-auto"
          trigger={({ open, toggle }) => (
            <button
              type="button"
              onClick={toggle}
              aria-expanded={open}
              data-demo="knowledge-group-by"
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-di-md border border-line-strong bg-surface px-2.5 py-1.5 text-[12.5px] text-ink hover:bg-sunken"
            >
              Group by: <b className="font-semibold">{column.label}</b>
              <Icon name="caret" className="size-3.5 text-ink-3" />
            </button>
          )}
        >
          {({ close }) => (
            <>
              {groupable.map((candidate) => (
                <button
                  key={candidate.key}
                  type="button"
                  title={`Group the documents by ${candidate.label}`}
                  onClick={() => {
                    onFieldChange(candidate.key);
                    setValueSearch("");
                    close();
                  }}
                  className={cn(
                    "flex w-full cursor-pointer items-center gap-2 rounded-di-sm px-2.25 py-1.75 text-left text-[12.5px]",
                    candidate.key === column.key
                      ? "bg-accent-soft font-semibold text-accent-softtext"
                      : "text-ink hover:bg-sunken",
                  )}
                >
                  <span className="truncate">{candidate.label}</span>
                  {/* One line here, not two: this list is a picker, and the
                      type is the tiebreaker between two similar field names
                      rather than something to read down the column. */}
                  <span className="ml-auto shrink-0 font-mono text-[10px] text-ink-3">
                    {candidate.type}
                  </span>
                </button>
              ))}
            </>
          )}
        </Dropdown>

        <div
          className="inline-flex overflow-hidden rounded-di-md border border-line-strong"
          role="group"
          aria-label="How to chart this field"
        >
          {VIZ_MODES.map((mode) => {
            const allowed = mode.key !== "dist" || numeric;
            return (
              <button
                key={mode.key}
                type="button"
                data-demo={`knowledge-viz-${mode.key}`}
                disabled={!allowed}
                aria-pressed={effectiveViz === mode.key}
                title={
                  allowed
                    ? mode.title
                    : `A distribution needs numbers. ${column.label} holds text, so pick a numeric column such as Fields filled.`
                }
                onClick={() => onVizChange(mode.key)}
                className={cn(
                  "px-2.5 py-1.5 text-[12.5px] font-semibold",
                  !allowed && "cursor-not-allowed opacity-45",
                  allowed && "cursor-pointer",
                  effectiveViz === mode.key
                    ? "bg-accent-soft text-accent-softtext"
                    : "bg-surface text-ink-2 hover:bg-sunken",
                )}
              >
                {mode.label}
              </button>
            );
          })}
        </div>

        <div className="flex-1" />

        <Dropdown
          align="right"
          panelClassName="w-[min(22rem,calc(100vw-3rem))]"
          trigger={({ open, toggle }) => (
            <LinkBtn
              onClick={toggle}
              aria-expanded={open}
              title="Search every value of this field"
            >
              {facets.values.length > MAX_FACET_CHIPS
                ? `See all ${facets.values.length} values`
                : "See all values"}
            </LinkBtn>
          )}
        >
          {() => (
            <>
              <div className="mb-2.5 flex items-center gap-1.5 rounded-di-md border border-line bg-sunken px-2 py-1.5 text-ink-3">
                <Icon name="search" className="size-3.5" />
                <input
                  value={valueSearch}
                  onChange={(event) => setValueSearch(event.target.value)}
                  placeholder="Search values…"
                  aria-label="Search values"
                  className="w-full border-0 bg-transparent text-[12.5px] text-ink outline-none"
                />
              </div>
              <p className="tnum mb-2 text-[12px] text-ink-2">
                {valueSearch.trim()
                  ? `${filteredValues.length} of ${facets.values.length} unique values match “${valueSearch.trim()}”`
                  : `${facets.values.length} unique values · ${facets.base} documents`}
              </p>
              <div className="max-h-75 overflow-auto">
                {filteredValues.length === 0 ? (
                  <p className="px-1 py-2 text-[12.5px] text-ink-3">
                    {valueSearch.trim()
                      ? "No value in this column contains that. Try a shorter word, or pick another column."
                      : "This column is empty in every document."}
                  </p>
                ) : (
                  filteredValues.map((facet) => (
                    <button
                      key={`${facet.missing}-${facet.value}`}
                      type="button"
                      onClick={() =>
                        onToggleValue(column, facet.value, facet.missing)
                      }
                      className={cn(
                        "flex w-full cursor-pointer items-center gap-2.5 rounded-di-sm px-2 py-1.5 text-left",
                        isSelected(facet)
                          ? "bg-accent-soft"
                          : "hover:bg-sunken",
                      )}
                    >
                      <span className="min-w-0 flex-1">
                        <span
                          className={cn(
                            "block truncate text-[12.5px]",
                            facet.missing ? "text-ink-3 italic" : "text-ink",
                          )}
                        >
                          {facetValueLabel(facet)}
                        </span>
                        <span className="mt-1 block h-1 w-full overflow-hidden rounded-full bg-sunken">
                          <span
                            className="block h-full rounded-full bg-accent"
                            style={{
                              width: `${Math.round((facet.count / max) * 100)}%`,
                            }}
                          />
                        </span>
                      </span>
                      <span className="tnum shrink-0 text-[12px] font-semibold text-ink-2">
                        {facet.count}
                      </span>
                    </button>
                  ))
                )}
              </div>
            </>
          )}
        </Dropdown>

        <LinkBtn
          className="text-ink-2"
          data-demo="knowledge-clear-filters"
          onClick={onClearFilters}
          title="Remove every filter"
        >
          Clear filters
        </LinkBtn>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {facets.values.length === 0 ? (
          <p className="text-[12.5px] text-ink-3">
            Nothing to group by — no document matches the current filters.
          </p>
        ) : (
          facets.values.slice(0, MAX_FACET_CHIPS).map((facet, index) => {
            const selected = isSelected(facet);
            return (
              <button
                key={`${facet.missing}-${facet.value}`}
                type="button"
                // Only the leading chip is anchored: a demo clicks one, and a
                // selector that matched eight would be ambiguous.
                data-demo={index === 0 ? "knowledge-facet-chip" : undefined}
                aria-pressed={selected}
                title={
                  selected
                    ? `Stop keeping ${facetValueLabel(facet)}`
                    : `Also keep documents where ${column.label} is ${facetValueLabel(facet)}`
                }
                onClick={() =>
                  onToggleValue(column, facet.value, facet.missing)
                }
                className={cn(
                  "flex min-w-26 max-w-57.5 cursor-pointer flex-col items-start gap-1 rounded-di-sm border px-2.5 py-1.75 text-left",
                  selected
                    ? "border-accent bg-accent-soft"
                    : "border-line bg-sunken hover:border-line-strong",
                )}
              >
                <span
                  className={cn(
                    "max-w-full truncate text-[12.5px] font-semibold",
                    facet.missing ? "text-ink-3 italic" : "text-ink",
                  )}
                >
                  {facetValueLabel(facet)}
                </span>
                <span className="tnum text-[11.5px] text-ink-2">
                  {facet.count}
                </span>
                <span className="block h-1 w-full overflow-hidden rounded-full bg-line-soft">
                  <span
                    className="block h-full rounded-full bg-accent"
                    style={{
                      width: `${Math.round((facet.count / max) * 100)}%`,
                    }}
                  />
                </span>
              </button>
            );
          })
        )}
      </div>

      <div className="mt-3.5 border-t border-line-soft pt-3">
        {effectiveViz === "cover" ? (
          <Coverage
            items={coverageItems(viewRows, visibleCols)}
            rowCount={viewRows.length}
          />
        ) : effectiveViz === "dist" ? (
          <Distribution
            histogram={histogram(viewRows, column)}
            label={column.label}
          />
        ) : effectiveViz === "share" && items.length > MAX_DONUT_SLICES ? (
          <>
            <VizNote>
              {column.label} takes {items.length} different values, too many to
              read as a share. Showing bars instead.
            </VizNote>
            <Bars items={items} total={facets.base} />
          </>
        ) : effectiveViz === "share" ? (
          <Donut items={items} total={facets.base} centerLabel="documents" />
        ) : (
          <Bars items={items} total={facets.base} />
        )}
      </div>
    </div>
  );
}
