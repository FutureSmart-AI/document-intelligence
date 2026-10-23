"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { buildApiUrl } from "@/platform/utils";
import axios from "@/platform/utils/axios";

import { APIS } from "./api";

/**
 * The conversations a collection has had.
 *
 * `GET /api/knowledge/v1/{knowledge_id}/sessions` is the one session store this
 * product has: page-based, searchable by `q`, and the same rows whether the
 * question came from this dashboard, a widget or the API. The agent UI splits
 * its list into dashboard / user-history / widget tabs because three different
 * services record those; there is nothing to split here.
 */

/**
 * Where a conversation came from, as the service records it.
 *
 * `null` on any session older than migration 044 — the service cannot say after
 * the fact, and says nothing rather than guessing. Note that `shared` is not one
 * of these: it is a *filter* the route derives from the collection's current
 * owner, never a value stored on a row.
 */
export type ConversationSource = "widget" | "dashboard" | "api";

export interface ConversationRow {
  session_id: string;
  /** `session_title` when one was set, else the first question, truncated. */
  title: string | null;
  exchange_count: number;
  user_id: string | null;
  /**
   * The caller's address, captured when the conversation started. `null` for a
   * widget visitor (there is no account) and for any pre-044 session (the id
   * cannot be resolved to an address afterwards).
   */
  user_email: string | null;
  /** Display name from the same session; fall back to the email, not the id. */
  user_name: string | null;
  source: ConversationSource | null;
  created_at: string | null;
  /** The most recent turn. `null` only for a session with no turns yet. */
  updated_at: string | null;
}

export interface ConversationPage {
  sessions: ConversationRow[];
  total_count: number;
  current_page: number;
  total_pages: number;
}

export const CONVERSATIONS_PAGE_SIZE = 20;

/**
 * The date filter's four choices, as the artifact lists them.
 *
 * Narrowed on the service, not here: `GET …/sessions` takes `from` as an ISO
 * 8601 instant and filters on when a session was created, so a filtered page is
 * a full page of matches. Filtering the twenty rows already in hand would hide
 * conversations that are simply on page two.
 */
export const CONVERSATION_RANGES = [
  { id: "all", label: "Filter by date range" },
  { id: "today", label: "Today" },
  { id: "7", label: "Last 7 days" },
  { id: "30", label: "Last 30 days" },
] as const;

export type ConversationRange = (typeof CONVERSATION_RANGES)[number]["id"];

/**
 * The `from` instant a range asks for, or nothing for "all".
 *
 * Today starts at midnight where the reader is, because that is what "today"
 * means to them; the rolling ranges count back from now rather than from a day
 * boundary, so "last 7 days" is seven days and not seven and a bit.
 */
export function conversationsFrom(
  range: ConversationRange,
  now: Date = new Date(),
): string | undefined {
  if (range === "all") return undefined;
  if (range === "today") {
    const start = new Date(now);
    start.setHours(0, 0, 0, 0);
    return start.toISOString();
  }
  const days = Number(range);
  return new Date(now.getTime() - days * 86_400_000).toISOString();
}

/**
 * One page of conversations, most recently active first.
 *
 * Every narrowing here is done by the service, in SQL: the account (`userId`),
 * the text (`search`), the window (`range`), the origin (`source`), whose
 * threads to leave out (`excludeUserId`), and the order. So one list is one
 * query — `total_count` is that list's real total and a page is a page of it,
 * rather than a page that has to be re-split and re-counted in the browser.
 */
export interface ConversationQuery {
  page?: number;
  pageSize?: number;
  search?: string;
  userId?: string | null;
  range?: ConversationRange;
  /**
   * Origin filter. Alongside the three stored origins the route accepts
   * `shared` — a dashboard conversation held by somebody other than this
   * collection's current owner — which it derives in SQL.
   *
   * A pre-044 session carries no origin and so matches *none* of these; it is
   * only listed when this is omitted. Anything counting on a complete list has
   * to account for that (see `useLegacyConversationCount`).
   */
  source?: ConversationSource | "shared" | null;
  /** Drops one person's own threads; matched on either the id or the address. */
  excludeUserId?: string | null;
  sort?: ConversationSort;
  order?: "asc" | "desc";
}

/** The keys the route sorts on; anything else falls back to `last_activity`. */
export type ConversationSort = "last_activity" | "questions" | "title" | "user";

export function useConversations(
  knowledgeId: string | null,
  options?: ConversationQuery,
): UseQueryResult<ConversationPage> {
  const page = options?.page ?? 1;
  const pageSize = options?.pageSize ?? CONVERSATIONS_PAGE_SIZE;
  const search = options?.search?.trim() || undefined;
  const userId = options?.userId || undefined;
  const source = options?.source || undefined;
  const excludeUserId = options?.excludeUserId || undefined;
  const sort = options?.sort;
  const order = options?.order;
  const from = conversationsFrom(options?.range ?? "all");

  return useQuery<ConversationPage>({
    queryKey: [
      "di-conversations",
      knowledgeId,
      page,
      pageSize,
      search ?? null,
      userId ?? null,
      options?.range ?? "all",
      source ?? null,
      excludeUserId ?? null,
      sort ?? null,
      order ?? null,
    ],
    enabled: Boolean(knowledgeId),
    refetchOnWindowFocus: false,
    queryFn: async () => {
      const response = await axios.get<ConversationPage>(
        buildApiUrl(APIS.SESSIONS, { knowledge_id: knowledgeId as string }),
        {
          params: {
            page,
            page_size: pageSize,
            ...(search ? { q: search } : {}),
            ...(userId ? { user_id: userId } : {}),
            ...(from ? { from } : {}),
            ...(source ? { source } : {}),
            ...(excludeUserId ? { exclude_user_id: excludeUserId } : {}),
            ...(sort ? { sort } : {}),
            ...(order ? { order } : {}),
          },
        },
      );
      return (
        response.data ?? {
          sessions: [],
          total_count: 0,
          current_page: page,
          total_pages: 1,
        }
      );
    },
  });
}

/** Deletes one conversation and its messages. */
export function useDeleteConversation(knowledgeId: string | null) {
  const client = useQueryClient();
  return useMutation<{ message?: string }, unknown, string>({
    mutationFn: async (sessionId) => {
      const response = await axios.delete(
        buildApiUrl(APIS.SESSION, {
          knowledge_id: knowledgeId as string,
          session_id: sessionId,
        }),
      );
      return response.data;
    },
    onSuccess: () => {
      client.invalidateQueries({
        queryKey: ["di-conversations", knowledgeId],
      });
    },
  });
}
