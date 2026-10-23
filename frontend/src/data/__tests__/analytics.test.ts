import { describe, expect, it } from "vitest";

import {
  adaptQueryLog,
  adaptScorecard,
  adaptTrends,
  KNOWLEDGE_ANALYTICS_APIS,
  KNOWLEDGE_FILTER_OPTIONS,
  trendsWindowParams,
} from "../analytics";

/**
 * The adapters are the whole reason this product can render the agent
 * dashboard's analytics page, so what they promise is pinned here.
 *
 * The payloads below are trimmed copies of live responses (collection
 * `1c2159e5…`, 2026-08-23) rather than invented shapes: the fields that differ
 * from the dashboard's own contract are exactly the ones a fixture written from
 * imagination would get right by accident.
 */

const SCORECARD = {
  knowledge_id: "kb-1",
  period: "month",
  since: "2026-07-24T06:36:23Z",
  until: "2026-08-23T06:36:23Z",
  previous_since: "2026-06-24T06:36:23Z",
  usage: {
    total_queries: 5,
    unique_users: 1,
    widget_queries: 0,
    bucket: "day",
    per_day: [
      { bucket: "2026-08-19T00:00:00Z", day: "2026-08-19", queries: 5 },
    ],
  },
  quality: {
    answered: 3,
    no_answer: 2,
    error: 1,
    success_rate: 60,
    feedback: { positive: 1, negative: 0, total: 1 },
  },
  perf: {
    avg_ms: 19783.4,
    p50_ms: 21217,
    p95_ms: 22819.2,
    by_node: [
      {
        node_name: "answer",
        provider: "openai",
        model: "gpt-5.6-terra",
        queries: 4,
        total_calls: 4,
        errors: 0,
        avg_latency_ms: 1200,
        p50_latency_ms: 1100,
        p95_latency_ms: 1800,
        avg_latency_s: 1.2,
        p50_latency_s: 1.1,
        p95_latency_s: 1.8,
        total_tokens: 2552,
        input_tokens: 2190,
        output_tokens: 362,
        total_cost: 0.0109,
      },
    ],
    slowest: [
      {
        chat_id: "c1",
        query: "show me all added document list",
        latency_ms: 23038,
        total_tokens: 604,
        trace_id: "t1",
      },
    ],
  },
  cost: {
    total_cost: 0.010905,
    avg_cost_per_query: 0.002181,
    total_tokens: 2552,
    input_tokens: 2190,
    output_tokens: 362,
    cost_complete: true,
    by_model: [
      { provider: "openai", model: "gpt-5.6-terra", queries: 4, cost: 0.0109 },
    ],
    by_node: [],
    most_expensive: [
      {
        chat_id: "c2",
        query: "which college",
        total_tokens: 682,
        cost_usd: 0.003967,
        trace_id: "t2",
      },
    ],
  },
  trend: { direction: "up" },
};

describe("adaptScorecard", () => {
  const card = adaptScorecard(SCORECARD);

  it("carries the collection id where the shared page reads the agent id", () => {
    expect(card.agent_id).toBe("kb-1");
  });

  it("counts both kinds of unanswered question as unanswered", () => {
    // The service splits them; the dashboard's `unanswered` means both, and
    // `failed_api_requests` means only the errored ones.
    expect(card.quality.unanswered).toBe(3);
    expect(card.quality.no_answer).toBe(2);
    expect(card.quality.failed_api_requests).toBe(1);
  });

  it("reports no token budget rather than an unknown one", () => {
    expect(card.quality.budget_exhausted).toBe(0);
  });

  it("keeps the previous window the toolbar labels its pills with", () => {
    expect(card.previous_since).toBe("2026-06-24T06:36:23Z");
  });

  it("reads per-day volume off `day`, not the bucket timestamp", () => {
    expect(card.usage.per_day).toEqual([{ day: "2026-08-19", queries: 5 }]);
  });

  it("does not claim spend is fully priced when the flag is absent", () => {
    const missing = adaptScorecard({
      ...SCORECARD,
      cost: { ...SCORECARD.cost, cost_complete: undefined },
    });
    expect(missing.cost.cost_complete).toBe(false);
  });

  it("survives a payload with no blocks at all", () => {
    const empty = adaptScorecard({});
    expect(empty.usage.total_queries).toBe(0);
    expect(empty.quality.feedback).toBeNull();
    expect(empty.perf.by_node).toEqual([]);
  });
});

describe("adaptTrends", () => {
  const trends = adaptTrends({
    knowledge_id: "kb-1",
    bucket: "day",
    buckets: 7,
    since: "2026-08-16T00:00:00Z",
    until: "2026-08-23T00:00:00Z",
    metrics: ["success_rate", "avg_ms", "cost", "failures", "made_up_metric"],
    series: [
      {
        bucket: "2026-08-19",
        success_rate: 60,
        avg_ms: 19783.4,
        p95_ms: null,
        cost: 0.010905,
        failures: 2,
        total_queries: null,
      },
    ],
    regression: null,
  });

  it("drops a metric name no chart can plot", () => {
    expect(trends.metrics).toEqual([
      "success_rate",
      "avg_ms",
      "cost",
      "failures",
    ]);
  });

  it("omits a null metric instead of plotting it as zero", () => {
    expect(trends.series[0].p95_ms).toBeUndefined();
    expect(trends.series[0].success_rate).toBe(60);
    // `total_queries` is required by the shared type; null becomes 0 there
    // because the charts count buckets with it.
    expect(trends.series[0].total_queries).toBe(0);
  });

  it("treats a missing regression as no regression", () => {
    expect(trends.regression.detected).toBe(false);
  });
});

describe("trendsWindowParams", () => {
  it("expresses a preset as a bucket count, never as a period", () => {
    // `/health/trends` has no `period`: its window is bucket length × count, so
    // sending a period falls through to the route's own `buckets=8` default and
    // plots eight weeks under a toolbar that says seven days.
    const week = trendsWindowParams({ mode: "preset", period: "week" });
    expect(week).toEqual({ buckets: 7 });
    expect(trendsWindowParams({ mode: "preset", period: "month" })).toEqual({
      buckets: 30,
    });
  });

  it("leaves `bucket` and `metrics` to the shared hook that owns them", () => {
    // `useTrends` sends both, and its params are merged over these — naming
    // them here would be overridden weight that reads like a decision.
    const params = trendsWindowParams({ mode: "preset", period: "week" });
    expect(params.bucket).toBeUndefined();
    expect(params.metrics).toBeUndefined();
  });

  it("caps the bucket count at what the route will return", () => {
    // The spec publishes `buckets` max 52; asking for more is a 422.
    expect(
      Number(trendsWindowParams({ mode: "preset", period: "month" }).buckets),
    ).toBeLessThanOrEqual(52);
  });

  it("passes a custom range through as dates", () => {
    expect(
      trendsWindowParams({
        mode: "custom",
        start: "2026-08-01",
        end: "2026-08-08",
      }),
    ).toMatchObject({ start_date: "2026-08-01", end_date: "2026-08-08" });
  });
});

describe("adaptQueryLog", () => {
  const log = adaptQueryLog({
    knowledge_id: "kb-1",
    period: "month",
    sort: "latency_ms",
    order: "desc",
    total_matching: 5,
    limit: 1,
    offset: 0,
    returned: 1,
    queries: [
      {
        chat_id: "c1",
        session_id: "s1",
        user_id: "u1",
        created_at: "2026-08-19T21:19:03Z",
        query: "show me all added document list",
        refined_query: "What is the list of all added documents?",
        route: "knowledge",
        services_used: ["knowledge"],
        status: "no_answer",
        outcome: "failure",
        is_answered: false,
        feedback: "positive",
        provider: "openai",
        model: "gpt-5.6-terra",
        from_widget: false,
        key_type: "session_or_api_key",
        latency_ms: 23038,
        input_tokens: 537,
        output_tokens: 67,
        total_tokens: 604,
        retry_count: 0,
        cost_usd: 0.002348,
        cost_complete: true,
        node_data: [],
        trace_id: "t1",
      },
    ],
  });

  const row = log.queries[0];

  it("renames the timestamp the tables sort and label by", () => {
    expect(row.timestamp).toBe("2026-08-19T21:19:03Z");
  });

  it("nests the flat per-question metrics", () => {
    expect(row.metrics.latency_ms).toBe(23038);
    expect(row.metrics.total_tokens).toBe(604);
    expect(row.metrics.cost_usd).toBeCloseTo(0.002348);
    expect(row.metrics.cost_complete).toBe(true);
  });

  it("reports no node trace rather than an empty one", () => {
    // An empty array would render an expandable trace with nothing in it.
    expect(row.metrics.node_data).toBeNull();
    // No per-node latency of our own is measured outside `node_data`.
    expect(row.metrics.service_calls).toBeNull();
  });

  it("keeps the service the row reports", () => {
    expect(row.services_used).toEqual(["knowledge"]);
  });
});

describe("the filter vocabularies", () => {
  it("offers no route filter, because the route ignores that parameter", () => {
    // Verified live: `route`, `outcome` and `min_cost_usd` all returned the
    // unfiltered log. An option that cannot filter is worse than none.
    expect(KNOWLEDGE_FILTER_OPTIONS.routes).toEqual([]);
  });

  it("offers the three statuses under the parameter that filters", () => {
    // `status` is the parameter this service honours; `outcome` is the one it
    // ignores, so the vocabulary must sit in `statuses` or the Status dropdown
    // would send a filter that silently does nothing.
    expect(KNOWLEDGE_FILTER_OPTIONS.statuses.map((o) => o.value)).toEqual([
      "success",
      "no_answer",
      "error",
    ]);
    expect(KNOWLEDGE_FILTER_OPTIONS.outcomes).toEqual([]);
  });

  it("offers only the feedback values that are distinct ratings", () => {
    const values = KNOWLEDGE_FILTER_OPTIONS.feedback.map((o) => o.value);
    expect(values).toContain("positive");
    expect(values).toContain("negative");
    expect(values).not.toContain("any");
    expect(values).not.toContain("rated");
  });
});

describe("the endpoint templates handed to the shared hooks", () => {
  it("renames the collection parameter to the one buildApiUrl fills", () => {
    // The shared hooks substitute `{agent_id}`; `APIS` must keep
    // `{knowledge_id}` so the contract test can check it against the spec.
    for (const template of Object.values(KNOWLEDGE_ANALYTICS_APIS)) {
      expect(template).toContain("{agent_id}");
      expect(template).not.toContain("{knowledge_id}");
    }
  });
});
