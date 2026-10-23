import type { ConversationRow } from "@/data/conversations";

/** A dated bucket of conversations, in the order the sidebar draws them. */
export interface SessionGroup {
  label: string;
  items: ConversationRow[];
}

const DAY_MS = 24 * 60 * 60 * 1000;

/** Midnight local time for a date, so "yesterday" means the calendar day. */
function startOfDay(date: Date): number {
  return new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate(),
  ).getTime();
}

/**
 * When the sidebar considers a conversation to have happened.
 *
 * The list arrives ordered by last activity, so the buckets are cut on the same
 * instant the order is: a thread answered this morning belongs under Today even
 * when it was opened last month. `created_at` is the fallback for a session with
 * no turns yet.
 */
const activityAt = (session: ConversationRow): string | null =>
  session.updated_at ?? session.created_at;

/**
 * Buckets conversations into Today / Yesterday / Previous 7 days / Older.
 *
 * Sessions with no usable timestamp are kept rather than dropped — every
 * pre-migration row has null dates, and a conversation the reader can see in the
 * list is better than a thread that silently disappears. They sort last, under
 * Older.
 */
export function groupSessions(
  sessions: ConversationRow[],
  now: Date = new Date(),
): SessionGroup[] {
  const today = startOfDay(now);
  const buckets: SessionGroup[] = [
    { label: "Today", items: [] },
    { label: "Yesterday", items: [] },
    { label: "Previous 7 days", items: [] },
    { label: "Older", items: [] },
  ];

  for (const session of sessions) {
    const time = Date.parse(activityAt(session) ?? "");
    if (Number.isNaN(time)) {
      buckets[3].items.push(session);
      continue;
    }
    const day = startOfDay(new Date(time));
    if (day === today) buckets[0].items.push(session);
    else if (day === today - DAY_MS) buckets[1].items.push(session);
    else if (day > today - 7 * DAY_MS) buckets[2].items.push(session);
    else buckets[3].items.push(session);
  }

  return buckets.filter((bucket) => bucket.items.length > 0);
}
