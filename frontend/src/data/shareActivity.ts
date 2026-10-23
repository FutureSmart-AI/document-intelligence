"use client";

import dayjs from "dayjs";
import relativeTimePlugin from "dayjs/plugin/relativeTime";

import {
  useConversations,
  type ConversationQuery,
  type ConversationRange,
  type ConversationRow,
  type ConversationSort,
} from "./conversations";

dayjs.extend(relativeTimePlugin);

/**
 * Share → Activity: the conversations somebody *else* had with this collection.
 *
 * One tab is one query. `GET …/sessions` filters on origin (`?source=`) and on
 * whose threads to leave out (`?exclude_user_id=`) and sorts, all in SQL, so
 * `total_count` is the tab's real total and a page is a page of that tab. There
 * is no splitting, re-counting or classifying left to do in the browser — an
 * earlier version of this screen did all three against a route that could not
 * filter, and a page of twenty could render three rows.
 *
 * `shared` is a filter, not a stored value: the route derives "held by somebody
 * other than this collection's current owner" itself. It is paired with
 * `exclude_user_id` anyway, because a reader who is an invited editor rather
 * than the owner would otherwise find their own threads on the list.
 *
 * The one thing the service cannot answer: a session recorded before its
 * origin migration carries `source: null` and matches *no* `?source=` value, so
 * it is absent from every tab. `useLegacyConversationCount` counts those, and
 * the screen says so rather than quietly listing fewer conversations than the
 * collection has had.
 */

/** The three ways a conversation reaches a shared collection. */
export const ACTIVITY_TABS = [
  {
    id: "shared",
    label: "Shared users",
    /** `shared`, not `dashboard`: the owner's own threads are not this screen. */
    source: "shared",
    note: "Questions asked by the people you shared this collection with. Your own conversations stay under Conversations.",
    empty: "No one has asked anything through your share link yet.",
    who: "Person",
    search: "Search person, email or question",
  },
  {
    id: "widget",
    label: "Widget",
    source: "widget",
    note: "Questions asked from the chat bubble on your website. Visitors are anonymous — the service keeps no account for them.",
    empty: "No conversations from the website bubble yet.",
    who: "Visitor",
    search: "Search visitor or question",
  },
  {
    id: "api",
    label: "API",
    source: "api",
    note: "Questions asked by your own code through a key. There is no person behind these — the key is the caller.",
    empty: "Nothing has been asked through an API key yet.",
    who: "Caller",
    search: "Search question",
  },
] as const satisfies ReadonlyArray<{
  id: string;
  label: string;
  source: NonNullable<ConversationQuery["source"]>;
  note: string;
  empty: string;
  who: string;
  search: string;
}>;

export type ActivityTab = (typeof ACTIVITY_TABS)[number]["id"];

export const activityTab = (id: ActivityTab) =>
  ACTIVITY_TABS.find((tab) => tab.id === id) ?? ACTIVITY_TABS[0];

/** The four windows the design offers, as the sessions route's `from`. */
export const ACTIVITY_RANGES = [
  { id: "all", label: "All time" },
  { id: "today", label: "Today" },
  { id: "7", label: "Last 7 days" },
  { id: "30", label: "Last 30 days" },
] as const;

export type ActivityRange = Extract<
  ConversationRange,
  (typeof ACTIVITY_RANGES)[number]["id"]
>;

export const rangeLabel = (range: ActivityRange): string =>
  ACTIVITY_RANGES.find((option) => option.id === range)?.label ?? "All time";

/** Table column → the key the route sorts on. */
export const ACTIVITY_SORT_KEYS = {
  who: "user",
  title: "title",
  questions: "questions",
  last: "last_activity",
} as const satisfies Record<string, ConversationSort>;

export type ActivityColumn = keyof typeof ACTIVITY_SORT_KEYS;

/** One row of the table: a conversation, and who had it. */
export interface ActivityConversation {
  sessionId: string;
  title: string;
  /** The name shown on the row. */
  who: string;
  /** Under it — the address, or what stands in for one. */
  whoSub: string;
  /** Hovered on the sub-label; the id is not shown outright. */
  userId: string | null;
  questions: number;
  lastIso: string;
  last: string;
  widget: boolean;
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * A person's name.
 *
 * `user_name` is what the identity provider supplied, and `user_email` is the
 * fallback the service's own docstring names. The id is the last resort and
 * only for a pre-migration session, where the service has no address to give:
 * an id that happens to be an address reads as a name, and anything else is
 * shown as the id it is rather than dressed up as a person.
 */
export function personName(row: {
  user_name?: string | null;
  user_email?: string | null;
  user_id?: string | null;
}): string {
  const name = row.user_name?.trim();
  if (name) return name;
  const email = row.user_email?.trim();
  if (email) return nameFromEmail(email);
  const id = row.user_id?.trim();
  if (!id) return "Anonymous";
  if (EMAIL.test(id)) return nameFromEmail(id);
  // The first segment rather than the first eight characters: an id is usually
  // a uuid, whose first segment is short anyway, and cutting one that is not
  // leaves a word chopped in half ("colleague-user" → "colleagu").
  return `User ${(id.split(/[-_]/)[0] || id).slice(0, 12)}`;
}

function nameFromEmail(email: string): string {
  const local = email.slice(
    0,
    email.indexOf("@") === -1 ? 0 : email.indexOf("@"),
  );
  const words = local
    .split(/[._-]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1));
  return words.join(" ") || email;
}

/** The label for a caller with no account — the bubble, or a key. */
export function visitorName(row: ConversationRow, tab: ActivityTab): string {
  const tail = (row.session_id || "").replace(/-/g, "").slice(-4) || "0000";
  return tab === "api" ? `Key call · ${tail}` : `Visitor · ${tail}`;
}

/** "2 hours ago" — with today's first minute and yesterday worded as read. */
export function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const at = dayjs(iso);
  if (!at.isValid()) return "—";
  const minutes = dayjs().diff(at, "minute");
  if (minutes < 1) return "Just now";
  const days = dayjs().startOf("day").diff(at.startOf("day"), "day");
  if (days === 1) return "Yesterday";
  return at.fromNow();
}

/**
 * One session → one table row, worded for the tab it is listed on.
 *
 * No classifying happens here any more: the service already returned only the
 * rows this tab asked for. What is left is presentation — a caller with no
 * account has no name to show, so the tab decides what to call them.
 */
export function toActivityRow(
  session: ConversationRow,
  tab: ActivityTab,
): ActivityConversation {
  const anonymous = tab !== "shared";
  const lastIso = session.updated_at || session.created_at || "";
  return {
    sessionId: session.session_id,
    title: session.title || "Untitled conversation",
    who: anonymous ? visitorName(session, tab) : personName(session),
    whoSub: anonymous
      ? tab === "api"
        ? "Through an API key"
        : "Anonymous website visitor"
      : (session.user_email ??
        // Pre-migration: the service has no address for this account, and the
        // row already shows the id as its name.
        "No address recorded"),
    userId: session.user_id,
    questions: session.exchange_count,
    lastIso,
    last: ago(lastIso),
    widget: tab === "widget",
  };
}

/**
 * How many conversations each tab holds, and how many the tabs cannot show.
 *
 * Four counting reads of one row each (`page_size=1`), so a tab can state its
 * total without its page being fetched. The fourth is the unfiltered total:
 * subtract the three origins from it and what remains are the sessions
 * recorded before the service tracked where a conversation came from, which no
 * `?source=` value matches.
 */
export function useActivityCounts(
  collectionId: string | null,
  options: { range: ActivityRange; excludeUserId?: string | null },
) {
  const shared = useTabCount(collectionId, "shared", options);
  const widget = useTabCount(collectionId, "widget", options);
  const api = useTabCount(collectionId, "api", options);
  const everything = useTabCount(collectionId, null, options);

  const counts = {
    shared: shared.data?.total_count ?? 0,
    widget: widget.data?.total_count ?? 0,
    api: api.data?.total_count ?? 0,
  };
  const isLoading =
    shared.isLoading ||
    widget.isLoading ||
    api.isLoading ||
    everything.isLoading;

  return {
    counts,
    isLoading,
    /** Listed on no tab: recorded before the service kept an origin. */
    unclassified: isLoading
      ? 0
      : Math.max(
          0,
          (everything.data?.total_count ?? 0) -
            counts.shared -
            counts.widget -
            counts.api,
        ),
  };
}

function useTabCount(
  collectionId: string | null,
  source: NonNullable<ConversationQuery["source"]> | null,
  options: { range: ActivityRange; excludeUserId?: string | null },
) {
  return useConversations(collectionId, {
    page: 1,
    pageSize: 1,
    range: options.range,
    source,
    excludeUserId: options.excludeUserId,
  });
}
