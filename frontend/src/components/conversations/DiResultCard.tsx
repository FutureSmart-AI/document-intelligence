"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import Icon, { type IconName } from "@/components/Icon";
import { ChatChart } from "@/platform/chat-core/charts";
import type { KnowledgeChartView } from "@/platform/components/conversations/knowledge/normalizeKnowledgeChart";
import { Markdown } from "@/platform/components/conversations/markdown";
import { downloadRowsAsCsv } from "@/platform/utils/csv";

import type { ChartDataframe } from "./chartDataframe";
import DiResultTable, { display } from "./DiResultTable";

/** A passage the answer was drawn from. */
export interface DiSource {
  title?: string;
  file_name?: string;
  page?: number | string;
  /** What the knowledge service calls the retrieved passage. */
  context?: string;
  summary?: string;
  content?: string;
  text?: string;
  score?: number;
}

type TabKey = "chart" | "data" | "sources" | "raw";

interface DiResultCardProps {
  /** The whole frame behind the answer, when the run returned one. */
  dataframe: ChartDataframe | null;
  /** The plot the run produced, when it produced one. */
  chart: KnowledgeChartView | null;
  /** The passages behind the answer, when the collection shows them. */
  sources?: DiSource[];
  /** The whole assistant message, dumped verbatim under Raw. */
  raw?: unknown;
  /** Whether the collection exposes the untouched API payload. */
  rawEnabled?: boolean;
}

/**
 * Columns folded away on arrival: the row's own key and the timestamp of its
 * last write, which push the columns a reader came for off the edge of the card.
 * Nothing is dropped — the Columns menu carries them, the export carries them,
 * and the button says how many are folded.
 */
function defaultHidden(columns: string[]): Record<string, boolean> {
  const noisy = columns.filter(
    (key) => key === "id" || key === "updated_at" || key.endsWith("_id"),
  );
  // Never fold everything away: a card with one column left reads as broken.
  if (!noisy.length || noisy.length >= columns.length) return {};
  return Object.fromEntries(noisy.map((key) => [key, true]));
}

const sourceLabel = (source: DiSource, index: number): string =>
  source.title || source.file_name || `Source ${index + 1}`;

/**
 * The passage itself.
 *
 * `context` first: it is the field `extractSources` fills for this service —
 * the old evidence panel read only `content`/`text`, which no knowledge answer
 * carries, so every card showed a file name over an empty box.
 */
const sourceBody = (source: DiSource): string =>
  source.context || source.summary || source.content || source.text || "";

/**
 * Everything one answer produced, behind one strip of tabs.
 *
 * The plot, the rows it was drawn from, the passages that justify it and the
 * untouched payload are four readings of a single run, so they sit in a single
 * card and cost one click each — rather than a result card stacked on a
 * separate collapsible evidence panel, which spent two boxes and a scroll on
 * what is one answer. The tab strip carries the view's own controls on its
 * right, so a result is one bar of chrome tall no matter which tab is open.
 */
export default function DiResultCard({
  dataframe,
  chart,
  sources = [],
  raw,
  rawEnabled = false,
}: DiResultCardProps) {
  const rows = useMemo(() => dataframe?.rows ?? [], [dataframe]);
  const allColumns = useMemo(() => dataframe?.columns ?? [], [dataframe]);

  // Null until a tab is picked, so the card can resolve its own opening view
  // from what the run actually produced.
  const [tab, setTab] = useState<TabKey | null>(null);
  const [query, setQuery] = useState("");
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const [columnsOpen, setColumnsOpen] = useState(false);
  const columnsRef = useRef<HTMLDivElement>(null);

  const columns = allColumns.filter((key) => !hidden[key]);

  // Filtering lives here rather than in the table, because the card's own row
  // count reports it: the strip reads "6 of 10 rows" the moment the filter box
  // narrows the result.
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((row) =>
      columns.some((key) => display(row[key]).toLowerCase().includes(needle)),
    );
    // `columns` is derived from `hidden` and the frame on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, hidden, query]);

  useEffect(() => {
    setQuery("");
    setHidden(defaultHidden(allColumns));
  }, [allColumns]);

  // Close the columns menu on an outside click, or it stays open over whatever
  // the reader scrolls to next.
  useEffect(() => {
    if (!columnsOpen) return undefined;
    const onPointerDown = (event: MouseEvent) => {
      if (!columnsRef.current?.contains(event.target as Node)) {
        setColumnsOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [columnsOpen]);

  const tabs: Array<{ key: TabKey; label: string; icon: IconName }> = [
    ...(chart
      ? [{ key: "chart" as const, label: "Chart", icon: "chart" as const }]
      : []),
    ...(rows.length
      ? [{ key: "data" as const, label: "Data", icon: "tbl" as const }]
      : []),
    ...(sources.length
      ? [{ key: "sources" as const, label: "Sources", icon: "file" as const }]
      : []),
    ...(rawEnabled
      ? [{ key: "raw" as const, label: "Raw", icon: "code" as const }]
      : []),
  ];

  // The run produced nothing to look at — no plot, no rows, no evidence, and a
  // collection that does not hand back its payload.
  if (!tabs.length) return null;

  // The card opens on the result — the plot, or the rows behind it. Sources and
  // Raw are what a reader asks for rather than what an answer greets them with,
  // so an answer carrying only those opens on no tab at all and waits for the
  // click, which is what the collapsible evidence panel used to buy.
  const opening =
    tabs.find((entry) => entry.key === "chart" || entry.key === "data")?.key ??
    null;
  const active = tab && tabs.some((entry) => entry.key === tab) ? tab : opening;

  const rowCountLabel =
    filtered.length !== rows.length
      ? `${filtered.length.toLocaleString()} of ${rows.length.toLocaleString()} rows`
      : dataframe?.totalRows !== undefined && dataframe.totalRows > rows.length
        ? `${rows.length.toLocaleString()} of ${dataframe.totalRows.toLocaleString()} rows`
        : `${rows.length.toLocaleString()} ${rows.length === 1 ? "row" : "rows"}`;

  // A lone Chart tab has no controls to hang off it, so the card opens straight
  // onto the plot rather than on a strip that says "Chart" over a chart.
  const showStrip = tabs.length > 1 || active !== "chart";

  return (
    <div className="overflow-hidden rounded-di-lg border border-line bg-surface shadow-card">
      {showStrip && (
        <div className="flex flex-wrap items-center gap-1.5 border-b border-line bg-sunken px-2">
          <div role="tablist" aria-label="Result view" className="flex">
            {tabs.map((entry) => (
              <button
                key={entry.key}
                type="button"
                role="tab"
                aria-selected={active === entry.key}
                onClick={() => setTab(entry.key)}
                className={`-mb-px inline-flex cursor-pointer items-center gap-1.5 border-0 border-b-2 bg-transparent px-2.5 py-2 text-[12px] font-semibold ${
                  active === entry.key
                    ? "border-accent text-accent-strong"
                    : "border-transparent text-ink-3 hover:text-ink-2"
                }`}
              >
                <Icon name={entry.icon} className="size-3.5" />
                {entry.label}
                {entry.key === "sources" && (
                  <span className="rounded-full bg-raised px-1.5 text-[10px] font-bold text-ink-3">
                    {sources.length}
                  </span>
                )}
              </button>
            ))}
          </div>

          <div className="flex-1" />

          {active === "data" && (
            <div className="flex items-center gap-1.5 py-1">
              <span className="text-[11px] text-ink-3">{rowCountLabel}</span>

              <div className="flex items-center gap-1.5 rounded-di-sm border border-line bg-surface px-2 py-0.5">
                <Icon name="search" className="size-3 flex-none text-ink-3" />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Filter rows"
                  aria-label="Filter rows"
                  className="w-24 border-0 bg-transparent text-[11.5px] text-ink outline-none placeholder:text-ink-3"
                />
              </div>

              <div className="relative" ref={columnsRef}>
                <button
                  type="button"
                  onClick={() => setColumnsOpen((open) => !open)}
                  aria-expanded={columnsOpen}
                  className="inline-flex cursor-pointer items-center gap-1.5 rounded-di-sm border border-line bg-surface px-2 py-1 text-[11px] font-semibold text-ink-2 hover:bg-raised"
                >
                  <Icon name="cols" className="size-3" />
                  Columns
                  {allColumns.length > columns.length && (
                    <span
                      className="rounded-full bg-raised px-1.5 text-[10px] font-bold text-ink-3"
                      title={`${allColumns.length - columns.length} of ${allColumns.length} columns hidden`}
                    >
                      {allColumns.length - columns.length}
                    </span>
                  )}
                </button>
                {columnsOpen && (
                  <div className="absolute top-8 right-0 z-20 flex max-h-72 w-45 flex-col gap-px overflow-y-auto rounded-di-md border border-line bg-surface p-1.5 shadow-pop">
                    {allColumns.map((key) => {
                      const shown = !hidden[key];
                      // The last visible column cannot be hidden — a table with
                      // no columns reads as "no rows" rather than "nothing
                      // selected".
                      const locked = shown && columns.length === 1;
                      return (
                        <button
                          key={key}
                          type="button"
                          disabled={locked}
                          aria-label={`${shown ? "Hide" : "Show"} ${key}`}
                          title={
                            locked
                              ? "At least one column stays visible"
                              : undefined
                          }
                          onClick={() =>
                            setHidden((current) => ({
                              ...current,
                              [key]: shown,
                            }))
                          }
                          className="flex cursor-pointer items-center gap-2 rounded-di-sm px-2 py-1.5 text-left text-xs text-ink-2 hover:bg-sunken disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <span
                            className={`grid size-3.5 flex-none place-items-center rounded-[3px] text-accent-contrast ${
                              shown
                                ? "bg-accent"
                                : "bg-transparent ring-1 ring-line ring-inset"
                            }`}
                          >
                            {shown && <Icon name="chk" className="size-2.5" />}
                          </span>
                          <span className="truncate font-mono text-[11.5px]">
                            {key}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>

              <button
                type="button"
                onClick={() => downloadRowsAsCsv(rows, "collection-result.csv")}
                title="Download every row of this result as CSV"
                className="inline-flex cursor-pointer items-center gap-1.5 rounded-di-sm border border-line bg-surface px-2 py-1 text-[11px] font-semibold text-ink-2 hover:bg-raised"
              >
                <Icon name="dn" className="size-3" />
                CSV
              </button>
            </div>
          )}
        </div>
      )}

      {active === "chart" && chart && (
        <div className="flex flex-col gap-2 px-3 pt-2.5 pb-3">
          <span className="inline-flex items-center gap-1.5 text-[11px] text-ink-3">
            <span className="font-semibold text-ink-2">Suggested chart:</span>
            {chart.chartSuggestion}
          </span>
          <ChatChart
            chartData={chart.chartData}
            chartSuggestion={chart.chartSuggestion}
          />
        </div>
      )}

      {active === "data" && (
        <DiResultTable
          rows={filtered}
          columns={columns}
          totalRows={rows.length}
          query={query}
        />
      )}

      {active === "sources" && (
        // Capped and scrolled, as Raw is: a live answer cites a dozen passages,
        // and a dozen expanded passages is a card taller than the window —
        // which is the stacked evidence panel's problem back again.
        <ol className="flex max-h-[320px] list-none flex-col gap-2 overflow-y-auto p-2.5">
          {sources.map((source, index) => (
            <li
              key={`${sourceLabel(source, index)}-${index}`}
              // `flex-none`, or the capped column shrinks each passage to a
              // header strip instead of scrolling the list.
              className="flex-none overflow-hidden rounded-di-md border border-line"
            >
              <div className="flex items-center gap-2 border-b border-line bg-sunken px-2.5 py-1">
                <Icon name="file" className="size-3 flex-none text-ink-3" />
                <span className="truncate text-[11px] font-semibold text-ink-2">
                  {sourceLabel(source, index)}
                </span>
                {source.page !== undefined && (
                  <span className="font-mono text-[10.5px] text-ink-3">
                    p. {source.page}
                  </span>
                )}
              </div>
              {sourceBody(source) && (
                <div className="max-h-36 overflow-y-auto bg-sunken px-2.5 py-2 text-[12px] leading-relaxed text-ink-2">
                  <Markdown>{sourceBody(source)}</Markdown>
                </div>
              )}
            </li>
          ))}
        </ol>
      )}

      {active === "raw" && (
        <div className="p-2.5">
          <pre className="m-0 max-h-[230px] overflow-auto rounded-di-md border border-line bg-sunken p-2.5 font-mono text-[11px] leading-relaxed whitespace-pre text-ink-2">
            {JSON.stringify(raw, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
