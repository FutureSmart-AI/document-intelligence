import dayjs from "dayjs";
import { describe, expect, it } from "vitest";

import type { ConversationRow } from "../conversations";
import {
  ACTIVITY_SORT_KEYS,
  ACTIVITY_TABS,
  ago,
  personName,
  toActivityRow,
  visitorName,
} from "../shareActivity";

/**
 * What is left to test once the service does the filtering.
 *
 * Classification is gone — `?source=` decides which tab a conversation is on,
 * in SQL. What remains in the browser is presentation, and the one judgement
 * inside it: what to call a person when the service has no name for them.
 */

const session = (over: Partial<ConversationRow>): ConversationRow => ({
  session_id: "ac50bcab-b846-48b0-9088-f72b8af98781",
  title: "Backend candidates with Kubernetes",
  exchange_count: 3,
  user_id: "c45844e8-b0f1-70d5-c97c-4ed460334d27",
  user_email: "priya.nair@futuresmart.ai",
  user_name: "Priya Nair",
  source: "dashboard",
  created_at: "2026-08-20T09:00:00Z",
  updated_at: "2026-08-26T09:00:00Z",
  ...over,
});

describe("toActivityRow", () => {
  it("shows the name and address the service now returns", () => {
    const row = toActivityRow(session({}), "shared");
    expect(row.who).toBe("Priya Nair");
    expect(row.whoSub).toBe("priya.nair@futuresmart.ai");
    expect(row.questions).toBe(3);
    // "Last activity" is the last turn, which the service stamps for real now.
    expect(row.lastIso).toBe("2026-08-26T09:00:00Z");
  });

  it("says an older conversation has no address rather than inventing one", () => {
    // Pre-migration: no origin, no address, only the account id.
    const row = toActivityRow(
      session({ user_email: null, user_name: null, source: null }),
      "shared",
    );
    expect(row.who).toBe("User c45844e8");
    expect(row.whoSub).toBe("No address recorded");
  });

  it("labels the callers who have no account, per tab", () => {
    const anonymous = session({
      user_id: null,
      user_email: null,
      user_name: null,
    });
    const bubble = toActivityRow({ ...anonymous, source: "widget" }, "widget");
    expect(bubble.who).toBe("Visitor · 8781");
    expect(bubble.whoSub).toBe("Anonymous website visitor");
    expect(bubble.widget).toBe(true);

    const key = toActivityRow({ ...anonymous, source: "api" }, "api");
    expect(key.who).toBe("Key call · 8781");
    expect(key.whoSub).toBe("Through an API key");
    // Only the bubble gets the widget wording on the read-only thread.
    expect(key.widget).toBe(false);
  });

  it("falls back to when it started only if there is no last turn", () => {
    const row = toActivityRow(
      session({ title: null, updated_at: null }),
      "shared",
    );
    expect(row.title).toBe("Untitled conversation");
    expect(row.lastIso).toBe("2026-08-20T09:00:00Z");
  });
});

describe("the tabs", () => {
  it("asks the service for one origin each", () => {
    expect(ACTIVITY_TABS.map((tab) => [tab.id, tab.source])).toEqual([
      // `shared`, not `dashboard`: the route derives "not the owner's" itself.
      ["shared", "shared"],
      ["widget", "widget"],
      ["api", "api"],
    ]);
  });

  it("maps every column to a sort key the route documents", () => {
    expect(Object.values(ACTIVITY_SORT_KEYS)).toEqual([
      "user",
      "title",
      "questions",
      "last_activity",
    ]);
  });
});

describe("personName", () => {
  it("prefers the name, then the address, and only then the id", () => {
    expect(personName({ user_name: "Priya Nair", user_email: "p@x.io" })).toBe(
      "Priya Nair",
    );
    expect(personName({ user_email: "priya.nair@futuresmart.ai" })).toBe(
      "Priya Nair",
    );
    expect(personName({ user_email: "recruiting@acme-retail.com" })).toBe(
      "Recruiting",
    );
    expect(personName({ user_id: "8f2a1c77-4d10-4a1e-9a11-2b" })).toBe(
      "User 8f2a1c77",
    );
    // A word is not chopped in half to make eight characters.
    expect(personName({ user_id: "colleague-user" })).toBe("User colleague");
    expect(personName({})).toBe("Anonymous");
  });
});

describe("visitorName", () => {
  it("labels an anonymous caller by the tail of their session", () => {
    const row = { session_id: "sess-ab8f2a" } as ConversationRow;
    expect(visitorName(row, "widget")).toBe("Visitor · 8f2a");
    expect(visitorName(row, "api")).toBe("Key call · 8f2a");
  });
});

describe("ago", () => {
  it("says nothing rather than guessing when there is no instant", () => {
    expect(ago(null)).toBe("—");
    expect(ago("not-a-date")).toBe("—");
  });

  it("words the last few minutes and yesterday the way a reader does", () => {
    expect(ago(new Date().toISOString())).toBe("Just now");
    // The same clock time a day back, so the assertion cannot land two
    // calendar days ago when the suite runs just after midnight.
    expect(ago(dayjs().subtract(1, "day").toISOString())).toBe("Yesterday");
  });
});
