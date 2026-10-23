"use client";

import { useState } from "react";

import Icon from "@/components/Icon";
import { Btn, EmptyNote, Input, Pill, Select } from "@/components/ui";
import {
  nextSort,
  SortableTh,
  type SortState,
} from "@/components/usage/UsageBits";
import { useConversations } from "@/data/conversations";
import { useUserId } from "@/data/session";
import {
  ACTIVITY_RANGES,
  ACTIVITY_SORT_KEYS,
  ACTIVITY_TABS,
  activityTab,
  rangeLabel,
  toActivityRow,
  useActivityCounts,
  type ActivityColumn,
  type ActivityConversation,
  type ActivityRange,
  type ActivityTab,
} from "@/data/shareActivity";
import { cn } from "@/platform/utils";

import { ACTIVITY_PAGE_SIZE } from "./constants";
import ShareActivityThread from "./ShareActivityThread";

/**
 * Activity — the conversations other people have had with this collection.
 *
 * Three lists, one per way in: the people you invited, the chat bubble on your
 * website, and your own code through a key. Each row is a whole conversation
 * rather than a single question — the unit a reader actually asks about ("what
 * did Priya want?") — and clicking one opens the thread read-only.
 *
 * One tab is one query. The origin filter, the owner exclusion, the search, the
 * window and the sort are all applied by the service, so the count on a tab is
 * that tab's real total, a page is a page of it, and sorting orders the result
 * set rather than the twenty rows in front of you.
 *
 * The owner's own conversations are not here. They are what the Conversations
 * screen is, and this screen exists to answer what came of sharing.
 */

export default function ShareActivity({
  collectionId,
}: {
  collectionId: string;
}) {
  const currentUserId = useUserId();
  const [tab, setTab] = useState<ActivityTab>("shared");
  const [search, setSearch] = useState("");
  const [range, setRange] = useState<ActivityRange>("all");
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<SortState<ActivityColumn> | null>(null);
  const [openSessionId, setOpenSessionId] = useState<string | null>(null);

  const meta = activityTab(tab);

  const conversations = useConversations(collectionId, {
    page,
    pageSize: ACTIVITY_PAGE_SIZE,
    search,
    range,
    source: meta.source,
    // `shared` already means "not the owner's", but a reader who is an invited
    // editor rather than the owner is not who that derives from — without this
    // they would find their own threads on the list.
    excludeUserId: currentUserId,
    sort: sort ? ACTIVITY_SORT_KEYS[sort.key] : undefined,
    order: sort?.dir === "asc" ? "asc" : undefined,
  });
  const counts = useActivityCounts(collectionId, {
    range,
    excludeUserId: currentUserId,
  });

  const rows = (conversations.data?.sessions ?? []).map((session) =>
    toActivityRow(session, tab),
  );
  const total = conversations.data?.total_count ?? 0;
  const totalPages = conversations.data?.total_pages ?? 1;
  const open = openSessionId
    ? (rows.find((row) => row.sessionId === openSessionId) ?? null)
    : null;
  const filtered = Boolean(search) || range !== "all";

  const switchTab = (next: ActivityTab) => {
    if (next === tab) return;
    setTab(next);
    setSort(null);
    setPage(1);
    setOpenSessionId(null);
  };

  /** Any change to what is being listed invalidates the page and the opened row. */
  const reset = () => {
    setPage(1);
    setOpenSessionId(null);
  };

  if (open) {
    return (
      <div className="grid gap-3.5">
        <div className="flex flex-wrap items-center gap-2.5">
          <Btn
            size="sm"
            onClick={() => setOpenSessionId(null)}
            startIcon={<Icon name="caret" className="size-4 rotate-90" />}
          >
            Back
          </Btn>
          <span className="min-w-0">
            <b className="block truncate text-[15px] text-ink">{open.title}</b>
            <span className="text-[12px] text-ink-2">
              {open.who} · {open.whoSub} · {open.questions}{" "}
              {open.questions === 1 ? "question" : "questions"} · {open.last}
            </span>
          </span>
          <Pill tone="neutral" className="ml-auto">
            Read-only
          </Pill>
        </div>

        <div className="h-[62vh] min-h-[320px] overflow-hidden rounded-di-lg border border-line bg-surface">
          <ShareActivityThread
            collectionId={collectionId}
            sessionId={open.sessionId}
            widget={open.widget}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="grid gap-3.5">
      <div className="flex flex-wrap items-center gap-2.5">
        <div
          role="tablist"
          aria-label="Whose conversations"
          className="inline-flex overflow-hidden rounded-di-md border border-line-strong"
        >
          {ACTIVITY_TABS.map((option) => (
            <button
              key={option.id}
              type="button"
              role="tab"
              aria-selected={tab === option.id}
              onClick={() => switchTab(option.id)}
              className={cn(
                "cursor-pointer border-0 border-r border-line px-3 py-[7px] text-[12.5px] font-semibold transition-colors last:border-r-0",
                tab === option.id
                  ? "bg-accent-soft text-accent-softtext"
                  : "bg-surface text-ink-2 hover:text-ink",
              )}
            >
              {option.label}
              {/* The service's own count for that tab, not the rows in hand. */}
              <span className="ml-1.5 font-bold opacity-75">
                {counts.isLoading ? "…" : counts.counts[option.id]}
              </span>
            </button>
          ))}
        </div>
        <p className="max-w-[560px] text-[12.5px] leading-[1.55] text-ink-2">
          {meta.note}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2.5">
        {/* The width is on the wrapper, not the field: `cn` here is a plain
            join, so a width class on `Input` would sit beside its own `w-full`
            and lose to whichever rule the stylesheet emits last. */}
        <div className="relative w-[320px] max-w-full">
          <Icon
            name="search"
            className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-ink-3"
          />
          <Input
            className="pl-8"
            value={search}
            aria-label="Search conversations"
            placeholder={meta.search}
            onChange={(event) => {
              setSearch(event.target.value);
              reset();
            }}
          />
        </div>
        <Select
          fit
          value={range}
          aria-label="Date range"
          onChange={(event) => {
            setRange(event.target.value as ActivityRange);
            reset();
          }}
        >
          {ACTIVITY_RANGES.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </Select>
        <Pill tone="count" className="tnum ml-auto">
          {total === 0
            ? "No conversations"
            : totalPages > 1
              ? `Showing ${rows.length} of ${total} conversations`
              : `${total} conversation${total === 1 ? "" : "s"}`}
        </Pill>
      </div>

      {conversations.isLoading ? (
        <EmptyNote>Reading what has been asked…</EmptyNote>
      ) : conversations.isError ? (
        <EmptyNote>These conversations could not be read.</EmptyNote>
      ) : !rows.length ? (
        <EmptyNote>
          {filtered ? (
            <>
              No conversations match this search or date range.
              <Btn
                size="sm"
                className="ml-2"
                onClick={() => {
                  setSearch("");
                  setRange("all");
                  reset();
                }}
              >
                Clear filters
              </Btn>
            </>
          ) : (
            meta.empty
          )}
        </EmptyNote>
      ) : (
        <div className="overflow-hidden rounded-di-lg border border-line bg-surface shadow-card">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] border-collapse text-left text-[13px]">
              <thead className="border-b border-line text-[11px] tracking-[0.06em] text-ink-3 uppercase">
                <tr>
                  <ActivityTh
                    columnKey="who"
                    label={meta.who}
                    sort={sort}
                    onSort={setSort}
                    onReset={reset}
                  />
                  <ActivityTh
                    columnKey="title"
                    label="Conversation"
                    sort={sort}
                    onSort={setSort}
                    onReset={reset}
                  />
                  <ActivityTh
                    numeric
                    columnKey="questions"
                    label="Questions asked"
                    sort={sort}
                    onSort={setSort}
                    onReset={reset}
                  />
                  <ActivityTh
                    columnKey="last"
                    label="Last activity"
                    sort={sort}
                    onSort={setSort}
                    onReset={reset}
                  />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <ActivityTr
                    key={row.sessionId}
                    row={row}
                    onOpen={() => setOpenSessionId(row.sessionId)}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2.5 border-t border-line px-3.5 py-2.5 text-[12.5px] text-ink-2">
            <span>
              {total} conversation{total === 1 ? "" : "s"} ·{" "}
              {rangeLabel(range).toLowerCase()}
              {totalPages > 1 ? ` · page ${page} of ${totalPages}` : ""}
            </span>
            {totalPages > 1 ? (
              <span className="flex items-center gap-2">
                <Btn
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => {
                    setPage((current) => Math.max(1, current - 1));
                    setOpenSessionId(null);
                  }}
                >
                  Previous
                </Btn>
                <Btn
                  size="sm"
                  disabled={page >= totalPages}
                  onClick={() => {
                    setPage((current) => Math.min(totalPages, current + 1));
                    setOpenSessionId(null);
                  }}
                >
                  Next
                </Btn>
              </span>
            ) : (
              <span>Click a row to read it — you cannot reply.</span>
            )}
          </div>
        </div>
      )}

      {/* Sessions older than the service's origin migration match no tab at
          all, so the three lists above genuinely do not add up to what this
          collection has had. Said out loud, once, rather than left as a
          shortfall the reader has to notice. */}
      {counts.unclassified > 0 && (
        <p className="text-[12px] text-ink-3">
          {counts.unclassified} older conversation
          {counts.unclassified === 1 ? " is" : "s are"} not listed on any tab:
          the service only began recording where a conversation came from
          recently, and it cannot say after the fact.
        </p>
      )}
    </div>
  );
}

/**
 * A sortable heading that re-asks the service.
 *
 * Three states, as the design draws them — ascending, descending, unsorted —
 * where unsorted means sending no sort at all and taking the route's own
 * default, most recently active first.
 */
function ActivityTh({
  columnKey,
  label,
  sort,
  onSort,
  onReset,
  numeric,
}: {
  columnKey: ActivityColumn;
  label: string;
  sort: SortState<ActivityColumn> | null;
  onSort: (next: SortState<ActivityColumn> | null) => void;
  onReset: () => void;
  numeric?: boolean;
}) {
  return (
    <SortableTh
      columnKey={columnKey}
      label={label}
      sort={sort}
      numeric={numeric}
      onSort={(key) => {
        onSort(nextSort(sort, key));
        onReset();
      }}
    />
  );
}

/** One conversation. The whole row opens it, so the whole row is the button. */
function ActivityTr({
  row,
  onOpen,
}: {
  row: ActivityConversation;
  onOpen: () => void;
}) {
  return (
    <tr
      // A row is a link to a thread, so it takes a click and a keystroke alike;
      // a table cannot hold a <button> that spans it without breaking the grid.
      tabIndex={0}
      role="button"
      title="Open this conversation (read-only)"
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        onOpen();
      }}
      className="cursor-pointer border-b border-line-soft last:border-0 hover:bg-sunken focus-visible:bg-sunken focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent"
    >
      <td className="max-w-[260px] px-3 py-3">
        <span className="grid gap-0.5">
          <span className="truncate font-semibold text-ink">{row.who}</span>
          <span
            className="truncate text-[11.5px] text-ink-3"
            title={row.userId ?? undefined}
          >
            {row.whoSub}
          </span>
        </span>
      </td>
      <td className="max-w-[420px] px-3 py-3">
        <span className="block truncate font-semibold text-ink">
          {row.title}
        </span>
      </td>
      <td className="tnum px-3 py-3 text-right">{row.questions}</td>
      <td className="px-3 py-3 text-ink-2" title={row.lastIso || undefined}>
        {row.last}
      </td>
    </tr>
  );
}
