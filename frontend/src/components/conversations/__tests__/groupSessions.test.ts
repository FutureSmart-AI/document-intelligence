import { describe, expect, it } from "vitest";

import type { ConversationRow } from "@/data/conversations";

import { groupSessions } from "../groupSessions";

const NOW = new Date(2026, 8, 2, 12, 0, 0);

/** Local-time timestamp: the buckets mean calendar days where the user is. */
const at = (year: number, month: number, day: number, hour = 12): string =>
  new Date(year, month, day, hour).toISOString();

const session = (
  id: string,
  updated: string | null,
  created: string | null = updated,
): ConversationRow => ({
  session_id: id,
  title: id,
  exchange_count: 1,
  user_id: null,
  user_email: null,
  user_name: null,
  source: "dashboard",
  created_at: created,
  updated_at: updated,
});

describe("groupSessions", () => {
  it("buckets by calendar day, not by elapsed hours", () => {
    // 23:00 yesterday is 13 hours ago but belongs under Yesterday.
    const groups = groupSessions(
      [
        session("now", at(2026, 8, 2, 9)),
        session("lateYesterday", at(2026, 8, 1, 23)),
      ],
      NOW,
    );
    expect(groups.map((group) => group.label)).toEqual(["Today", "Yesterday"]);
    expect(groups[1].items[0].session_id).toBe("lateYesterday");
  });

  it("cuts on last activity, so an old thread answered today reads as today", () => {
    const groups = groupSessions(
      [session("revived", at(2026, 8, 2, 9), at(2026, 6, 1, 9))],
      NOW,
    );
    expect(groups.map((group) => group.label)).toEqual(["Today"]);
  });

  it("falls back to when the conversation was opened", () => {
    const groups = groupSessions(
      [session("noTurns", null, at(2026, 8, 2, 9))],
      NOW,
    );
    expect(groups.map((group) => group.label)).toEqual(["Today"]);
  });

  it("separates the last week from older threads", () => {
    const groups = groupSessions(
      [
        session("week", at(2026, 7, 29, 10)),
        session("old", at(2026, 6, 1, 10)),
      ],
      NOW,
    );
    expect(groups.map((group) => group.label)).toEqual([
      "Previous 7 days",
      "Older",
    ]);
  });

  it("drops empty buckets", () => {
    const groups = groupSessions([session("a", at(2026, 8, 2, 1))], NOW);
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("Today");
  });

  it("keeps a pre-migration session with no dates at all", () => {
    const groups = groupSessions([session("x", null, null)], NOW);
    expect(groups.map((group) => group.label)).toEqual(["Older"]);
    expect(groups[0].items[0].session_id).toBe("x");
  });

  it("preserves the order it was given inside a bucket", () => {
    const groups = groupSessions(
      [
        session("first", at(2026, 8, 2, 10)),
        session("second", at(2026, 8, 2, 9)),
      ],
      NOW,
    );
    expect(groups[0].items.map((item) => item.session_id)).toEqual([
      "first",
      "second",
    ]);
  });
});
