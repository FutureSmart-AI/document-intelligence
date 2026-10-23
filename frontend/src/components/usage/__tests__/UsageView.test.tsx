import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  isoDate,
  type AnalyticsQuery,
  type AnalyticsResponse,
  type LimitsScreen,
  type ServiceOptions,
  type UsageStatus,
} from "@/data/usage";

import UsageView from "../UsageView";

/**
 * The Usage screen.
 *
 * Only the hooks are mocked — the period arithmetic, the formatting and the
 * sorting are the real ones, because those are what turn the service's answer
 * into the claim on screen. What each test pins is either a number the screen
 * asserts, or the request it made to get it: this surface shipped once already
 * reading routes that did not exist, and every symptom of that was a plausible
 * zero rather than an error.
 */

const analyticsSpy = vi.fn();
const collectionsSpy = vi.fn();

// The view opens on the tab `?tab=` names; with no param it opens on Overview.
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/data/usage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/data/usage")>();
  return {
    ...actual,
    useUsageStatus: () => ({ data: statusData, isLoading: false }),
    useServiceOptions: () => ({ data: serviceOptions }),
    useLimitsScreen: () => ({
      data: limitsScreen,
      isLoading: false,
      isError: false,
    }),
    useAnalytics: (query: AnalyticsQuery, options?: { enabled?: boolean }) => {
      analyticsSpy(query, options);
      if (options?.enabled === false)
        return { data: undefined, isLoading: false };
      return {
        data: answerFor(query),
        isLoading: false,
        isError: analyticsErrors,
      };
    },
  };
});

vi.mock("@/data/collections", () => ({
  useCollections: () => {
    collectionsSpy();
    return { collections, isLoading: false, isError: false };
  },
}));

const K1 = "9e7e5767-5da0-44e2-ab7e-06c0d70b382c";
const K2 = "3ca68247-87f5-4304-900c-33f8ecfa52e4";

let collections: Array<{ id: string; name: string }> = [];
let statusData: UsageStatus | undefined;
let analyticsErrors = false;
let serviceOptions: unknown;

/** The Limits tab reads its own route; the screen only has to place it. */
const limitsScreen: LimitsScreen = {
  owner_id: "owner@futuresmart.ai",
  tier: { name: "Trial", tier_type: "trial", is_default: true },
  window: { start: "2026-08-01T00:00:00+00:00", end: "2026-09-01" },
  own_credentials: [],
  exceeded: [],
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
          note: "Every collection counts, active or paused.",
          window: "current",
          percent: 30,
          enforced: true,
        },
      ],
    },
  ],
};

const status: UsageStatus = {
  owner_id: "owner@futuresmart.ai",
  period: {
    start: "2026-08-01T00:00:00+00:00",
    end: "2026-09-01T00:00:00+00:00",
  },
  limits: {
    max_agents: null,
    max_indexed_characters: 1_000_000,
    max_knowledge_items: 50,
    max_conversations_per_month: null,
    max_messages_per_month: 10_000,
    max_llm_tokens_per_month: 500_000,
    max_documents: 400,
    max_urls: 100,
    max_messages_per_day: 200,
    max_cost_usd_per_month: 25,
  },
  current_usage: {
    indexed_characters: 250_000,
    knowledge_items: 12,
    agents: 4,
    conversations_this_month: 3,
    messages_this_month: 2_500,
    llm_tokens_this_month: 3_000,
    documents: 90,
    urls: 8,
    messages_today: 40,
    cost_usd_this_month: 3.8,
  },
  remaining: {
    indexed_characters: 750_000,
    knowledge_items: 38,
    agents: null,
    conversations: null,
    messages: 7_500,
    llm_tokens: 497_000,
    documents: 310,
    urls: 92,
    messages_today: 160,
    cost_usd: 21.2,
  },
};

/**
 * An account whose whole window is priced at nothing — a new one, or one
 * running entirely on its own keys, which the service does not bill for.
 */
let costsReported = true;

/**
 * An operation the service ran and priced at nothing — parsing and vector
 * writes are often free while the model call beside them is billed.
 */
let freeOperationReported = false;

const metrics = (requests: number, pages: number, rawCost: number) => {
  const cost = costsReported ? rawCost : 0;
  return {
    request_count: requests,
    total_tokens: requests * 100,
    input_tokens: requests * 80,
    output_tokens: requests * 20,
    llama_parse_page_count: pages,
    processed_weblink_count: 0,
    // The service prices its work in lines, and the screen hangs each line on the
    // figure it was spent on, so the breakdown is part of the fixture.
    cost: {
      total_cost_usd: cost,
      llm_cost_usd: cost * 0.75,
      llm_input_tokens_cost_usd: cost * 0.5,
      llm_output_tokens_cost_usd: cost * 0.25,
      document_processing_cost_usd: cost * 0.2,
      embedding_cost_usd: cost * 0.05,
    },
  };
};

/**
 * The window before the current one spent half as much, so every delta is a
 * clean +100%. Told apart by its end date rather than by which call it is: the
 * current window always ends today, the comparison one ends where it begins.
 */
function answerFor(query: AnalyticsQuery): AnalyticsResponse {
  const groupBy = query.group_by ?? [];
  const isPrevious = query.date_range.end !== isoDate(new Date());

  const base: AnalyticsResponse = {
    owner_id: "owner@futuresmart.ai",
    executed_at: "2026-08-20T12:00:00+00:00",
    date_range: query.date_range,
    group_by: groupBy,
    totals: metrics(
      isPrevious ? 6 : 12,
      isPrevious ? 30 : 60,
      isPrevious ? 0.6 : 1.2,
    ),
    knowledge_overview_metrics: {
      total_files_processed: isPrevious ? 12 : 24,
      total_char_count: isPrevious ? 6_000 : 12_000,
      total_audio_seconds: 0,
      total_llama_parse_pages: isPrevious ? 30 : 60,
      total_weblink_pages: 0,
      total_text_count: 0,
      total_embedding_tokens: 900,
      total_vectors_indexed: 300,
      cost: { total_cost_usd: isPrevious ? 0.6 : 1.2 },
    },
    data: [],
    total_groups: 0,
    has_more: false,
  };

  if (groupBy.includes("knowledge_id")) {
    base.data = [
      // Deliberately smallest-first, so a sort has something to do, and with
      // one id the board has no name for.
      { dimensions: { knowledge_id: K2 }, metrics: metrics(4, 20, 0.4) },
      { dimensions: { knowledge_id: K1 }, metrics: metrics(8, 40, 0.8) },
      {
        dimensions: { knowledge_id: "unknown-id" },
        metrics: metrics(1, 5, 0.1),
      },
    ];
  } else if (groupBy.includes("operation")) {
    base.data = [
      {
        dimensions: { service_type: "knowledge", operation: "llm_generation" },
        metrics: metrics(9, 0, 0.9),
      },
      {
        dimensions: { service_type: "knowledge", operation: "data_ingestion" },
        metrics: metrics(3, 60, 0.3),
      },
      ...(freeOperationReported
        ? [
            {
              dimensions: {
                service_type: "knowledge",
                operation: "vector_db_write",
              },
              metrics: metrics(2, 0, 0),
            },
          ]
        : []),
    ];
  } else if (query.time_bucket) {
    base.data = [
      // Out of order on purpose: the chart must read oldest-first regardless.
      {
        dimensions: { time_bucket: "2026-08-20" },
        metrics: metrics(5, 25, 0.5),
      },
      {
        dimensions: { time_bucket: "2026-08-18" },
        metrics: metrics(4, 20, 0.4),
      },
      {
        dimensions: { time_bucket: "2026-08-19" },
        metrics: metrics(3, 15, 0.3),
      },
    ];
  }
  return base;
}

beforeEach(() => {
  vi.clearAllMocks();
  collections = [
    { id: K1, name: "Candidate Resumes" },
    { id: K2, name: "Vendor Contracts" },
  ];
  statusData = status;
  analyticsErrors = false;
  costsReported = true;
  freeOperationReported = false;
  // The catalogue as `analytics/service-options` returns it: one service type,
  // its five operations, and the functional labels nested under them.
  serviceOptions = {
    service_types: [
      {
        service_type: "knowledge",
        display_name: "Knowledge",
        operations: [
          {
            operation: "data_ingestion",
            display_name: "Data ingestion",
            labels: [
              { label: "file_processing", display_name: "File processing" },
            ],
          },
          {
            operation: "llm_call",
            display_name: "LLM call",
            labels: [{ label: "rag_agent", display_name: "RAG agent" }],
          },
          {
            operation: "embedding_generation",
            display_name: "Embedding generation",
            labels: [
              { label: "content_embedding", display_name: "Content embedding" },
            ],
          },
          { operation: "vector_db_write", display_name: "Vector DB write" },
          {
            operation: "llm_generation",
            display_name: "LLM generation",
            labels: [
              { label: "generate_answer", display_name: "Generate answer" },
            ],
          },
        ],
      },
    ],
    all_operations: [
      "data_ingestion",
      "llm_call",
      "embedding_generation",
      "vector_db_write",
      "llm_generation",
    ],
    // Platform-wide, as the service returns it: `chart_generation` and
    // `db_query_execution` are other products' labels, nested under service
    // types this account never records.
    all_labels: [
      "chart_generation",
      "content_embedding",
      "db_query_execution",
      "file_processing",
      "generate_answer",
      "rag_agent",
    ],
  };
});

/** Every analytics request the screen made, in call order. */
const queries = (): AnalyticsQuery[] =>
  analyticsSpy.mock.calls.map((call) => call[0] as AnalyticsQuery);

/** The requests the screen actually issued, i.e. not the disabled ones. */
const enabledQueries = (): AnalyticsQuery[] =>
  analyticsSpy.mock.calls
    .filter((call) => (call[1] as { enabled?: boolean })?.enabled !== false)
    .map((call) => call[0] as AnalyticsQuery);

describe("the overview tab", () => {
  it("leads with tokens, requests and what each of them cost", () => {
    render(<UsageView />);
    expect(screen.getByText("1,200")).toBeInTheDocument(); // total tokens
    expect(screen.getByText("960")).toBeInTheDocument(); // input tokens
    expect(screen.getByText("240")).toBeInTheDocument(); // output tokens
    expect(screen.getByText("12")).toBeInTheDocument(); // requests
    // The money is the service's own breakdown line, not the total repeated.
    expect(screen.getAllByText("$0.90").length).toBeGreaterThan(0); // llm cost
    expect(screen.getByText("$0.60")).toBeInTheDocument(); // input token cost
  });

  it("groups the secondary figures the way the artifact does", () => {
    render(<UsageView />);
    expect(screen.getByText("Documents")).toBeInTheDocument();
    expect(screen.getByText("Web")).toBeInTheDocument();
    expect(screen.getByText("Making documents searchable")).toBeInTheDocument();
    expect(screen.getByText("24")).toBeInTheDocument(); // files processed
    expect(screen.getByText("12.0K")).toBeInTheDocument(); // characters read
    expect(screen.getAllByText("900").length).toBeGreaterThan(0); // embedding tokens
  });

  /**
   * The deltas are the "compared with the previous period" note made good: two
   * real answers, not a number invented to look like growth.
   */
  it("compares each figure with the window before it", () => {
    render(<UsageView />);
    // Every headline doubled against the previous window.
    expect(screen.getAllByText("▲ 100.0%").length).toBeGreaterThanOrEqual(4);
  });

  it("asks for the window and the window of the same length before it", () => {
    render(<UsageView />);
    const ranges = enabledQueries().map((query) => query.date_range);
    const today = isoDate(new Date());
    const current = ranges.find((range) => range.end === today);
    const previous = ranges.find((range) => range.end !== today);
    expect(current).toBeDefined();
    expect(previous).toBeDefined();
    // Adjacent and the same length, which is what the screen promises in words.
    expect(previous?.end).toBe(current?.start);
    const days = (range: { start: string; end: string }) =>
      (Date.parse(range.end) - Date.parse(range.start)) / 86_400_000;
    expect(days(previous!)).toBe(days(current!));
  });

  it("puts the trend in date order however the service returned it", () => {
    const { container } = render(<UsageView />);
    // The axis is labelled at its ends; every bucket carries its own figure.
    const buckets = Array.from(container.querySelectorAll("title"))
      .map((title) => title.textContent)
      .filter((text) => text?.includes("08-"));
    expect(buckets).toEqual(["400 · 08-18", "300 · 08-19", "500 · 08-20"]);
  });

  /**
   * `service_type` is `knowledge` on every event this product records, so a
   * ring grouped by it is one full-circle slice. `operation` is the dimension
   * that varies, and the one the reader is trying to tell apart.
   */
  it("splits the window's cost across the operations that spent it", () => {
    render(<UsageView />);
    const donut = screen
      .getByText("Operation Breakdown", { selector: "b" })
      .closest("div")?.parentElement as HTMLElement;
    expect(within(donut).getByText("75%")).toBeInTheDocument();
    expect(within(donut).getByText("25%")).toBeInTheDocument();
    // Named as the catalogue names it, not as the raw code.
    expect(within(donut).getByText("LLM generation")).toBeInTheDocument();
  });

  /**
   * An operation billed at nothing still ran, and one this account has never
   * run is still an operation the service records. Neither can be drawn — an
   * arc of zero length is nothing — but dropping them reads as though the model
   * call were the only step, so they are listed under the ring at 0%.
   */
  it("lists every operation the service records, priced or not", () => {
    freeOperationReported = true;
    render(<UsageView />);
    const donut = screen
      .getByText("Operation Breakdown", { selector: "b" })
      .closest("div")?.parentElement as HTMLElement;

    // The catalogue's five, by their display names — including the two the
    // window's answer carries no row for at all.
    for (const name of [
      "LLM generation",
      "Data ingestion",
      "Vector DB write",
      "Embedding generation",
      "LLM call",
    ]) {
      expect(within(donut).getByText(name)).toBeInTheDocument();
    }
    expect(within(donut).getAllByText("0%")).toHaveLength(3);
    // The priced operations still split the ring between them.
    expect(within(donut).getByText("75%")).toBeInTheDocument();
    expect(within(donut).getByText("25%")).toBeInTheDocument();
  });

  it("asks for the operation grouping once, and draws both cards from it", () => {
    render(<UsageView />);
    const grouped = enabledQueries().filter(
      (query) => !query.time_bucket && query.group_by?.includes("operation"),
    );
    expect(grouped).toHaveLength(1);
    expect(grouped[0].group_by).toEqual(["service_type", "operation"]);
  });

  /**
   * A window the service priced at nothing — a new account, or one running
   * entirely on its own keys — would otherwise draw an empty ring. The card
   * falls back to token share and says in its own heading which it is drawing,
   * so a reader is never told a share of cost that was never measured.
   */
  it("falls back to token share when nothing in the window was priced", () => {
    costsReported = false;
    render(<UsageView />);
    const donut = screen
      .getByText("Operation Breakdown", { selector: "b" })
      .closest("div")?.parentElement as HTMLElement;

    expect(
      within(donut).getByLabelText("Token share by operation"),
    ).toBeInTheDocument();
    // 900 tokens against 300: the ring still splits, rather than emptying.
    expect(within(donut).getByText("75%")).toBeInTheDocument();
    expect(within(donut).getByText("25%")).toBeInTheDocument();
  });

  /**
   * The answer is grouped by service type as well as operation, so the row
   * carries what did the work. It reads `Knowledge` down the column for this
   * product — that is the only service type its events record — and it is the
   * catalogue's own word for it, not a spelling invented here.
   */
  it("names the service each operation was recorded under", () => {
    render(<UsageView />);
    const table = screen
      .getByText("By operation", { selector: "b" })
      .closest("div")?.parentElement as HTMLElement;

    expect(
      within(table).getByRole("button", { name: /Service/ }),
    ).toBeInTheDocument();
    const services = within(table)
      .getAllByRole("row")
      .slice(1)
      .map((row) => row.querySelectorAll("td")[0]?.textContent);
    expect(services).toEqual(["Knowledge", "Knowledge"]);
  });

  it("sorts the operation breakdown on a column heading", async () => {
    render(<UsageView />);
    const table = screen
      .getByText("By operation", { selector: "b" })
      .closest("div")?.parentElement as HTMLElement;

    // Service leads the row, so the operation is the second cell.
    const names = () =>
      within(table)
        .getAllByRole("row")
        .slice(1)
        .map((row) => row.querySelectorAll("td")[1]?.textContent);

    // The service answered biggest-first; a sort has to actually reorder.
    expect(names()).toEqual(["LLM generation", "Data ingestion"]);

    const heading = within(table).getByRole("button", { name: /Requests/ });
    await userEvent.click(heading);
    expect(names()).toEqual(["Data ingestion", "LLM generation"]);

    await userEvent.click(heading);
    expect(names()).toEqual(["LLM generation", "Data ingestion"]);

    await userEvent.click(heading);
    expect(within(table).getByText(/· unsorted/)).toBeInTheDocument();
  });

  it("breaks the spend down by operation, with each one's share", () => {
    render(<UsageView />);
    const row = screen.getByRole("row", { name: /LLM generation/ });
    expect(within(row).getByText("$0.90")).toBeInTheDocument();
    expect(within(row).getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("12 requests · $1.20")).toBeInTheDocument();
  });
});

describe("the collections tab", () => {
  it("asks the service to group by knowledge base", async () => {
    render(<UsageView />);
    await userEvent.click(screen.getByRole("button", { name: "Collections" }));
    expect(
      enabledQueries().some((query) =>
        query.group_by?.includes("knowledge_id"),
      ),
    ).toBe(true);
  });

  it("names each collection from the board, and says so when it cannot", async () => {
    render(<UsageView />);
    await userEvent.click(screen.getByRole("button", { name: "Collections" }));

    expect(
      screen.getByRole("row", { name: /Candidate Resumes/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("row", { name: /Vendor Contracts/ }),
    ).toBeInTheDocument();
    // An id the board has no name for is still listed, not dropped.
    expect(screen.getByRole("row", { name: /Unnamed/ })).toBeInTheDocument();
  });

  it("totals the rows in its footer", async () => {
    render(<UsageView />);
    await userEvent.click(screen.getByRole("button", { name: "Collections" }));
    // 8 + 4 + 1 requests, 40 + 20 + 5 pages, 0.8 + 0.4 + 0.1 cost.
    expect(
      screen.getByText("13 requests · 65 pages · $1.30"),
    ).toBeInTheDocument();
    expect(screen.getByText(/3 collections/)).toBeInTheDocument();
  });

  it("sorts a column on click, and clears it on the third", async () => {
    render(<UsageView />);
    await userEvent.click(screen.getByRole("button", { name: "Collections" }));

    const names = () =>
      screen
        .getAllByRole("row")
        .slice(1)
        .map((row) => row.querySelector("td")?.textContent);

    const heading = screen.getByRole("button", { name: /Cost/ });
    await userEvent.click(heading);
    expect(names()).toEqual([
      "Unnamed",
      "Vendor Contracts",
      "Candidate Resumes",
    ]);

    await userEvent.click(heading);
    expect(names()).toEqual([
      "Candidate Resumes",
      "Vendor Contracts",
      "Unnamed",
    ]);

    await userEvent.click(heading);
    expect(screen.getByText(/· unsorted/)).toBeInTheDocument();
  });
});

describe("the limits tab", () => {
  it("shows the account's allowances", async () => {
    render(<UsageView />);
    await userEvent.click(screen.getByRole("button", { name: "Limits" }));
    expect(
      screen.getByRole("heading", { name: "Your Plan" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Collections you can create")).toBeInTheDocument();
  });

  /**
   * Every row there is counted over its own fixed window — what is held right
   * now, the day, the month — so a period control beside it would claim to
   * move a window it cannot, and the analytics queries behind it answer a
   * question the tab is not asking.
   */
  it("offers no reporting period, and reads no analytics for it", async () => {
    render(<UsageView />);
    await userEvent.click(screen.getByRole("button", { name: "Limits" }));

    expect(
      screen.queryByRole("button", { name: "Last 30 days" }),
    ).not.toBeInTheDocument();
    analyticsSpy.mockClear();
    await userEvent.click(screen.getByRole("button", { name: "Limits" }));
    expect(enabledQueries()).toHaveLength(0);
  });
});

describe("the source control", () => {
  /**
   * The one rule the service was explicit about: Total is its own unfiltered
   * call. Some events — embeddings, vector writes — carry no key type at all,
   * so they answer to neither filter and platform + owner comes up short of the
   * truth (267 + 2 against 336 requests, live, 2026-08-27). Costs happened to
   * add up that day, which is what makes deriving it dangerous.
   */
  it("sends no key type at all for Total", () => {
    render(<UsageView />);
    for (const query of enabledQueries()) {
      expect(query.filters?.key_type).toBeUndefined();
    }
  });

  it("files the key type inside filters, not beside the date range", async () => {
    render(<UsageView />);
    await userEvent.selectOptions(
      screen.getByLabelText("Consumption source"),
      "owner",
    );

    const last = enabledQueries().at(-1);
    expect(last?.filters).toEqual({ key_type: "owner-key" });
    // The route ignores an unknown top-level field and answers 200 with the
    // whole window, so the wrong placement looked like a working control.
    expect(last).not.toHaveProperty("key_filter");
  });

  it("maps platform to the platform key", async () => {
    render(<UsageView />);
    await userEvent.selectOptions(
      screen.getByLabelText("Consumption source"),
      "platform",
    );
    expect(enabledQueries().at(-1)?.filters?.key_type).toBe("platform-key");
  });

  /**
   * It sits with the period because it does the period's job: both narrow every
   * section at once. A source that only reached the Custom Filters tab is the
   * defect this replaces — the Overview quietly ignored it.
   */
  it("narrows every section on the screen, not one tab", async () => {
    render(<UsageView />);
    await userEvent.selectOptions(
      screen.getByLabelText("Consumption source"),
      "owner",
    );

    const queried = enabledQueries().slice(-4);
    expect(queried.length).toBeGreaterThan(1);
    for (const query of queried) {
      expect(query.filters?.key_type).toBe("owner-key");
    }
  });

  it("says which reading is on screen", async () => {
    render(<UsageView />);
    await userEvent.selectOptions(
      screen.getByLabelText("Consumption source"),
      "platform",
    );
    expect(screen.getByText(/Platform Consumptions only/)).toBeInTheDocument();
  });

  /** A limit is enforced against what was served, whichever key paid for it. */
  it("is not offered on the limits tab", async () => {
    render(<UsageView />);
    await userEvent.click(screen.getByRole("button", { name: "Limits" }));
    expect(
      screen.queryByLabelText("Consumption source"),
    ).not.toBeInTheDocument();
  });
});

describe("the custom filters tab", () => {
  it("sends the chosen operation and label on the query", async () => {
    render(<UsageView />);
    await userEvent.click(
      screen.getByRole("button", { name: "Custom Filters" }),
    );

    await userEvent.selectOptions(
      screen.getByLabelText("Operation"),
      "llm_generation",
    );
    await userEvent.selectOptions(
      screen.getByLabelText("Functional label"),
      "generate_answer",
    );

    expect(enabledQueries().at(-1)?.filters).toEqual({
      operations: ["llm_generation"],
      labels: ["generate_answer"],
    });
  });

  /**
   * `knowledge_id` is a single string on `filters`, not one of the arrays
   * beside it, and the service only started answering it on 2026-08-26 — the
   * shape is worth pinning, because the older backend answered the wrong one
   * with a 422 and the wrong shape reads as an empty period.
   */
  it("filters to one collection by id", async () => {
    render(<UsageView />);
    await userEvent.click(
      screen.getByRole("button", { name: "Custom Filters" }),
    );

    // Every collection on the board is offered, not only the ones that spent.
    expect(
      within(screen.getByLabelText("Collection"))
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["All collections", "Candidate Resumes", "Vendor Contracts"]);

    await userEvent.selectOptions(screen.getByLabelText("Collection"), K2);
    expect(enabledQueries().at(-1)?.filters).toEqual({ knowledge_id: K2 });
  });

  /**
   * `service_type` is one value on this product, so a Service picker could only
   * narrow to everything. Which key paid is the screen's Source control.
   */
  it("offers no service or key picker of its own", async () => {
    render(<UsageView />);
    await userEvent.click(
      screen.getByRole("button", { name: "Custom Filters" }),
    );
    expect(screen.queryByLabelText("Service")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Key")).not.toBeInTheDocument();
  });

  /**
   * `ServiceTypeOption.operations` is a list of objects while the
   * `all_operations` beside it is a list of strings. Typing the first as the
   * second put `[object Object]` in the picker and filed a filter value the
   * service could never match — visible only as an empty screen.
   */
  it("lists the catalogue's five operations by their display names", async () => {
    render(<UsageView />);
    await userEvent.click(
      screen.getByRole("button", { name: "Custom Filters" }),
    );

    const options = within(screen.getByLabelText("Operation")).getAllByRole(
      "option",
    );
    expect(options.map((option) => option.textContent)).not.toContain(
      "[object Object]",
    );
    expect(options.map((option) => option.textContent)).toEqual([
      "All operations",
      "Data ingestion",
      "LLM call",
      "Embedding generation",
      "Vector DB write",
      "LLM generation",
    ]);
    // The value filed is the operation, not its display name.
    expect(
      options.map((option) => (option as HTMLOptionElement).value),
    ).toEqual([
      "",
      "data_ingestion",
      "llm_call",
      "embedding_generation",
      "vector_db_write",
      "llm_generation",
    ]);
  });

  it("offers every label the catalogue nests, until an operation narrows them", async () => {
    render(<UsageView />);
    await userEvent.click(
      screen.getByRole("button", { name: "Custom Filters" }),
    );

    const labels = () =>
      within(screen.getByLabelText("Functional label"))
        .getAllByRole("option")
        .map((option) => (option as HTMLOptionElement).value);

    // The five nested under this service's operations, in catalogue order —
    // and not `chart_generation` or `db_query_execution`, which belong to other
    // products and would answer with an empty window.
    expect(labels()).toEqual([
      "",
      "file_processing",
      "rag_agent",
      "content_embedding",
      "generate_answer",
    ]);

    await userEvent.selectOptions(
      screen.getByLabelText("Operation"),
      "llm_generation",
    );
    // A label belongs to its operation; a pair that never co-occurs would read
    // on screen as "no usage" rather than as an impossible filter.
    expect(labels()).toEqual(["", "generate_answer"]);
    expect(
      within(screen.getByLabelText("Functional label"))
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["All labels", "Generate answer"]);
  });

  /**
   * A catalogue that nests no labels at all leaves the picker with nothing to
   * offer, and the flat list — imprecise as it is — beats an empty control.
   */
  it("falls back to the flat label list when the catalogue nests none", async () => {
    const catalogue = serviceOptions as ServiceOptions;
    serviceOptions = {
      ...catalogue,
      service_types: catalogue.service_types.map((entry) => ({
        ...entry,
        operations: (entry.operations ?? []).map(
          ({ labels: _labels, ...rest }) => rest,
        ),
      })),
    } satisfies ServiceOptions;
    render(<UsageView />);
    await userEvent.click(
      screen.getByRole("button", { name: "Custom Filters" }),
    );

    expect(
      within(screen.getByLabelText("Functional label"))
        .getAllByRole("option")
        .map((option) => (option as HTMLOptionElement).value),
    ).toEqual([
      "",
      "chart_generation",
      "content_embedding",
      "db_query_execution",
      "file_processing",
      "generate_answer",
      "rag_agent",
    ]);
  });

  it("clears a stale label when the operation changes", async () => {
    render(<UsageView />);
    await userEvent.click(
      screen.getByRole("button", { name: "Custom Filters" }),
    );

    await userEvent.selectOptions(
      screen.getByLabelText("Operation"),
      "llm_generation",
    );
    await userEvent.selectOptions(
      screen.getByLabelText("Functional label"),
      "generate_answer",
    );
    await userEvent.selectOptions(
      screen.getByLabelText("Operation"),
      "data_ingestion",
    );

    expect(screen.getByLabelText("Functional label")).toHaveValue("");
    expect(enabledQueries().at(-1)?.filters).toEqual({
      operations: ["data_ingestion"],
    });
  });

  /**
   * The catalogue is global; the grouped answer is this account's. Falling back
   * to it keeps the picker usable when `service-options` cannot be read at all.
   */
  it("falls back to the operations the account has actually used", async () => {
    serviceOptions = undefined;
    render(<UsageView />);
    await userEvent.click(
      screen.getByRole("button", { name: "Custom Filters" }),
    );

    expect(
      within(screen.getByLabelText("Operation"))
        .getAllByRole("option")
        .map((option) => (option as HTMLOptionElement).value),
    ).toEqual(["", "llm_generation", "data_ingestion"]);
  });

  /** The other tabs are unfiltered — a filter left behind would silently skew them. */
  it("does not carry its filters back to the overview", async () => {
    render(<UsageView />);
    await userEvent.click(
      screen.getByRole("button", { name: "Custom Filters" }),
    );
    await userEvent.selectOptions(
      screen.getByLabelText("Operation"),
      "llm_call",
    );
    await userEvent.click(screen.getByRole("button", { name: "Overview" }));

    expect(enabledQueries().at(-1)?.filters).toBeUndefined();
  });

  /** The Source control is the screen's, so it survives the tab change. */
  it("keeps the source when the tab changes", async () => {
    render(<UsageView />);
    await userEvent.selectOptions(
      screen.getByLabelText("Consumption source"),
      "owner",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Custom Filters" }),
    );
    await userEvent.selectOptions(
      screen.getByLabelText("Operation"),
      "llm_call",
    );
    expect(enabledQueries().at(-1)?.filters).toEqual({
      operations: ["llm_call"],
      key_type: "owner-key",
    });

    await userEvent.click(screen.getByRole("button", { name: "Overview" }));
    expect(enabledQueries().at(-1)?.filters).toEqual({ key_type: "owner-key" });
  });
});

describe("the reporting period", () => {
  it("re-reads over the window that was picked", async () => {
    render(<UsageView />);
    const before = enabledQueries().at(0)?.date_range.start;

    await userEvent.click(screen.getByRole("button", { name: "Last 7 days" }));

    const after = enabledQueries().at(-1)?.date_range.start;
    expect(after).not.toBe(before);
    // Seven days is a narrower window, so it starts later than thirty.
    expect(String(after) > String(before)).toBe(true);
  });

  it("buckets ninety days by week rather than by day", async () => {
    render(<UsageView />);
    await userEvent.click(screen.getByRole("button", { name: "Last 90 days" }));
    const bucketed = enabledQueries().filter((query) => query.time_bucket);
    expect(bucketed.at(-1)?.time_bucket).toBe("week");
  });
});

describe("what it does with nothing to show", () => {
  it("does not fetch the tab it is not on", () => {
    render(<UsageView />);
    const disabled = analyticsSpy.mock.calls.filter(
      (call) => (call[1] as { enabled?: boolean })?.enabled === false,
    );
    // The per-collection grouping is not read until that tab is opened.
    expect(disabled).toHaveLength(1);
    expect((disabled[0][0] as AnalyticsQuery).group_by).toEqual([
      "knowledge_id",
    ]);
    expect(queries().length).toBeGreaterThan(disabled.length);
  });

  it("says so plainly when usage cannot be read", () => {
    analyticsErrors = true;
    render(<UsageView />);
    expect(
      screen.getByText(/Usage could not be read for this account/),
    ).toBeInTheDocument();
  });

  it("still renders the screen when the account has spent nothing", () => {
    collections = [];
    render(<UsageView />);
    expect(
      screen.getByRole("heading", { name: "Account Overview" }),
    ).toBeInTheDocument();
  });
});
