import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type LimitsScreen } from "@/data/usage";

import PlanLimits from "../PlanLimits";

/**
 * The Limits tab.
 *
 * Everything pinned here is a claim the tab makes on the reader's behalf, and
 * every one of them is the service's judgement rather than this screen's:
 * whether a row is capped at all, whether its ceiling is lifted, and whether
 * going over stops anything. Those three are what a reader acts on, and each
 * has a wrong rendering that reads as fact — a 0% bar on an uncapped row says
 * "you are out", and a "Blocking" chip on a reporting row invents a
 * consequence that does not exist.
 */

let screenData: LimitsScreen | undefined;
let state: { isLoading: boolean; isError: boolean } = {
  isLoading: false,
  isError: false,
};

vi.mock("@/data/usage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/data/usage")>();
  return {
    ...actual,
    useLimitsScreen: () => ({ data: screenData, ...state }),
  };
});

const answer: LimitsScreen = {
  owner_id: "owner@futuresmart.ai",
  tier: { name: "Trial", tier_type: "trial", is_default: true },
  window: { start: "2026-08-01T00:00:00+00:00", end: "2026-09-01" },
  own_credentials: ["groq", "openai"],
  exceeded: ["max_documents", "max_cost_usd_per_month"],
  groups: [
    {
      group: "Workspace",
      items: [
        {
          key: "max_agents",
          label: "Collections you can create",
          used: 3,
          limit: 10,
          unit: "collections",
          maximum: "10 collections on Trial",
          note: "Every collection in this workspace counts, active or paused.",
          window: "current",
          lifted: false,
          percent: 30,
          enforced: true,
        },
        {
          // Live since the caps were added, but not yet backfilled on older
          // accounts — the ordinary path, not an edge case.
          key: "max_urls",
          label: "Links you can hold",
          used: 42,
          limit: null,
          unit: "links",
          maximum: "No limit on this account",
          note: "One row per link added.",
          window: "current",
          lifted: false,
          percent: null,
          enforced: true,
        },
        {
          key: "max_documents",
          label: "Documents you can hold",
          used: 500,
          limit: 500,
          unit: "documents",
          maximum: "500 documents on Trial",
          note: "A sub-cap of the combined workspace item limit.",
          window: "current",
          lifted: false,
          percent: 100,
          enforced: true,
        },
      ],
    },
    {
      group: "Activity",
      items: [
        {
          key: "max_messages_per_day",
          label: "Questions today",
          used: 18,
          limit: 100,
          unit: "questions",
          maximum: "100 questions a day on Trial",
          note: "Counts every question asked of any collection.",
          window: "calendar_day",
          lifted: false,
          percent: 18,
          enforced: true,
        },
        {
          key: "max_llm_tokens_per_month",
          label: "Words read and written",
          used: 90_000,
          limit: 500_000,
          unit: "tokens",
          maximum: "500,000 on Trial",
          note: "Covered by your own key.",
          window: "calendar_month",
          lifted: true,
          percent: null,
          enforced: false,
        },
        {
          key: "max_cost_usd_per_month",
          label: "Estimated spend this month",
          used: 3.8,
          limit: 25,
          unit: "USD",
          maximum: "$25 a month on Trial",
          note: "What this account's work is estimated to have cost.",
          window: "calendar_month",
          lifted: false,
          percent: 15.2,
          enforced: false,
        },
      ],
    },
  ],
};

beforeEach(() => {
  screenData = structuredClone(answer);
  state = { isLoading: false, isError: false };
});

/** The row a label belongs to, so a chip is read against its own allowance. */
const rowFor = (label: string): HTMLElement =>
  screen.getByText(label).parentElement?.parentElement as HTMLElement;

describe("the limits tab", () => {
  it("draws each group the service sends, in its own words", () => {
    render(<PlanLimits />);
    expect(screen.getByText("Workspace")).toBeInTheDocument();
    expect(screen.getByText("Activity")).toBeInTheDocument();
    expect(screen.getByText("Collections you can create")).toBeInTheDocument();
    expect(screen.getByText("Documents you can hold")).toBeInTheDocument();
    expect(screen.getByText("Links you can hold")).toBeInTheDocument();
    expect(screen.getByText("Questions today")).toBeInTheDocument();
  });

  it("reads the percentage off the answer rather than recomputing it", () => {
    render(<PlanLimits />);
    // 3 of 10 is 30%: the same number the service used to decide `exceeded`.
    expect(
      screen.getByRole("img", { name: "3 of 10 collections used, 30%" }),
    ).toBeInTheDocument();
  });

  /**
   * A cap the service has not backfilled yet arrives as `limit: null`. Drawing
   * that as a 0% bar tells the reader they are out of an allowance that does
   * not exist.
   */
  it("says Unlimited, and draws no bar, where there is no ceiling", () => {
    render(<PlanLimits />);
    const row = rowFor("Links you can hold");
    expect(row).toHaveTextContent("42 links · Unlimited");
    expect(
      screen.queryByRole("img", { name: /42 links/ }),
    ).not.toBeInTheDocument();
  });

  it("draws no bar on a lifted row, and still states the ceiling", () => {
    render(<PlanLimits />);
    const row = rowFor("Words read and written");
    expect(row).toHaveTextContent("90,000 tokens · not capped");
    expect(row).toHaveTextContent("Lifted by your own key");
    expect(
      screen.getByText(/Maximum: 500,000 on Trial — not applied to you\./),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("img", { name: /90,000 tokens/ }),
    ).not.toBeInTheDocument();
  });

  /**
   * `enforced` is the difference between a row that stops answers and a row
   * that only reports, and it can flip server-side without a deploy — so it is
   * read from each answer, never from a list kept here.
   */
  it("separates a blocking allowance from one that only reports", () => {
    render(<PlanLimits />);
    expect(rowFor("Documents you can hold")).toHaveTextContent("Blocking");

    const spend = rowFor("Estimated spend this month");
    expect(spend).toHaveTextContent("No headroom left");
    expect(spend).not.toHaveTextContent("Blocking");
  });

  it("banners only the exceeded allowance that actually refuses work", () => {
    render(<PlanLimits />);
    const banners = screen.getAllByRole("status");
    expect(banners).toHaveLength(1);
    expect(banners[0]).toHaveTextContent("Documents you can hold reached.");
  });

  it("prices a money row in dollars rather than as a count", () => {
    render(<PlanLimits />);
    // "$3.80 of $25.00", never "3.8 of 25 USD".
    expect(rowFor("Estimated spend this month")).toHaveTextContent(
      "$3.80 of $25.00",
    );
  });

  it("says when each row's count starts again, per row", () => {
    render(<PlanLimits />);
    expect(rowFor("Questions today")).toHaveTextContent("Resets at 00:00 UTC.");
    expect(rowFor("Estimated spend this month")).toHaveTextContent(
      "Resets on the 1st.",
    );
    expect(rowFor("Collections you can create")).toHaveTextContent(
      "Counted right now, across the whole account.",
    );
  });

  it("names the plan and which providers are on the account's own keys", () => {
    render(<PlanLimits />);
    expect(screen.getByText("Trial plan")).toBeInTheDocument();
    expect(
      screen.getByText("Using your own keys for Groq and OpenAI"),
    ).toBeInTheDocument();
  });

  it("says so when every request is served on FutureSmart's keys", () => {
    screenData = { ...answer, own_credentials: [] };
    render(<PlanLimits />);
    expect(
      screen.getByText("Running entirely on FutureSmart keys"),
    ).toBeInTheDocument();
  });

  it("states that the period and filters do not apply here", () => {
    render(<PlanLimits />);
    expect(
      screen.getByText(/The reporting period does not apply on this tab/),
    ).toBeInTheDocument();
  });

  it("says so plainly when the plan cannot be read", () => {
    screenData = undefined;
    state = { isLoading: false, isError: true };
    render(<PlanLimits />);
    expect(
      screen.getByText(/Your plan could not be read for this account/),
    ).toBeInTheDocument();
  });

  it("has words in its empty state", () => {
    screenData = { ...answer, groups: [] };
    render(<PlanLimits />);
    expect(
      screen.getByText("No allowances are set on this account."),
    ).toBeInTheDocument();
  });
});
