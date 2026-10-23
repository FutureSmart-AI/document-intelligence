import type { Scorecard, ScorecardNode } from "@/platform/analytics/types";

/**
 * Answer models, derived from the scorecard's per-node aggregates.
 *
 * The service reports one row per **(step, model)** pair — `perf.by_node` and
 * `cost.by_node` are the same objects read from one query — plus a per-model
 * cost roll-up in `cost.by_model`. The Model performance tab needs both views of
 * that: one row per model (totalled across the steps it ran on) and one row per
 * model *on a chosen step*. Both are computed here, as plain functions, so the
 * arithmetic is testable and the component only lays it out.
 *
 * Two things this deliberately does not do, because the data does not support
 * them:
 *
 * - **No averaged percentiles.** `p50`/`p95` are real per (step, model) and are
 *   shown there. A model's overall row cannot average them — the mean of two
 *   p95s is not a p95 — so it reports its slowest step instead, which is a real
 *   measurement of the same worry.
 * - **No answer rate per model.** The service meters `errors` against calls, not
 *   answered-versus-unanswered per model: a question can go unanswered with
 *   every call succeeding. So the column is error-free calls, and it says so.
 */

/** One model, totalled over every step it ran on. */
export interface ModelRow {
  key: string;
  provider: string;
  model: string;
  /** Questions this model was used on, as the service counts them. */
  answers: number;
  /** LLM calls, which exceeds `answers` when a step retried. */
  calls: number;
  errors: number;
  /** Share of the window's answers, 0–100. */
  freq: number;
  /** Call-weighted mean of the model's per-step average latency. */
  avgMs: number | null;
  /** The step this model is slowest on, by p95 — a real number, not a mean. */
  slowest: { step: string; p95Ms: number } | null;
  costPerAnswer: number | null;
  totalCost: number | null;
  /** Calls that did not error, 0–100, or null when nothing is metered. */
  errorFreeRate: number | null;
  tokensInPerCall: number | null;
  tokensOutPerCall: number | null;
  /** The steps it ran on, in the order the service listed them. */
  steps: string[];
}

/** One model's numbers on a single step. */
export interface StepModelRow {
  key: string;
  provider: string;
  model: string;
  runs: number;
  /** Share of that step's calls, 0–100. */
  freq: number;
  avgMs: number | null;
  p50Ms: number | null;
  p95Ms: number | null;
  costPerRun: number | null;
  errorFreeRate: number | null;
  tokensIn: number | null;
  tokensOut: number | null;
}

const num = (value: number | null | undefined): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const nodeKey = (node: ScorecardNode) =>
  `${node.provider ?? "—"}/${node.model ?? "—"}`;

const modelLabel = (node: ScorecardNode) => node.model ?? "Unnamed model";

/**
 * Every node row the scorecard carries, de-duplicated.
 *
 * `perf.by_node` and `cost.by_node` come from one query and are usually the
 * same list, but either can be empty on a given window — so both are read and
 * merged on (step, provider, model) rather than one being trusted.
 */
export function nodeRows(scorecard: Scorecard | undefined): ScorecardNode[] {
  const merged = new Map<string, ScorecardNode>();
  for (const node of [
    ...(scorecard?.perf?.by_node ?? []),
    ...(scorecard?.cost?.by_node ?? []),
  ]) {
    const key = `${node.node_name}|${nodeKey(node)}`;
    const existing = merged.get(key);
    // Later fields fill gaps in earlier ones; neither list overrides a value
    // the other already reported.
    merged.set(key, existing ? { ...node, ...existing } : node);
  }
  return [...merged.values()];
}

/** The steps (graph nodes) this window recorded, in first-seen order. */
export function stepNames(scorecard: Scorecard | undefined): string[] {
  const names: string[] = [];
  for (const node of nodeRows(scorecard)) {
    const name = node.node_name || "";
    if (name && !names.includes(name)) names.push(name);
  }
  return names;
}

export function modelRows(scorecard: Scorecard | undefined): ModelRow[] {
  const nodes = nodeRows(scorecard);
  const byModel = new Map<string, ScorecardNode[]>();
  for (const node of nodes) {
    const key = nodeKey(node);
    byModel.set(key, [...(byModel.get(key) ?? []), node]);
  }

  // The cost roll-up is per model and independent of the node lists, so a
  // window that reports it but no nodes still gets a table.
  const costByModel = new Map(
    (scorecard?.cost?.by_model ?? []).map((entry) => [
      `${entry.provider ?? "—"}/${entry.model ?? "—"}`,
      entry,
    ]),
  );
  for (const key of costByModel.keys()) {
    if (!byModel.has(key)) byModel.set(key, []);
  }

  const rows: ModelRow[] = [...byModel.entries()].map(([key, group]) => {
    const rollup = costByModel.get(key);
    const [provider, model] = key.split("/");

    const calls = group.reduce(
      (sum, node) => sum + (num(node.total_calls) ?? 0),
      0,
    );
    const errors = group.reduce(
      (sum, node) => sum + (num(node.errors) ?? 0),
      0,
    );
    // A question that used three steps is one answer, not three, so the node
    // counts are maxed rather than summed.
    const nodeAnswers = group.reduce(
      (most, node) => Math.max(most, num(node.queries) ?? 0),
      0,
    );
    const answers = rollup?.queries ?? nodeAnswers;

    const weighted = group.reduce(
      (acc, node) => {
        const avg = num(node.avg_latency_ms);
        const nodeCalls = num(node.total_calls) ?? 0;
        if (avg === null || nodeCalls <= 0) return acc;
        return { ms: acc.ms + avg * nodeCalls, calls: acc.calls + nodeCalls };
      },
      { ms: 0, calls: 0 },
    );

    const slowest = group.reduce<ModelRow["slowest"]>((worst, node) => {
      const p95 = num(node.p95_latency_ms);
      if (p95 === null) return worst;
      if (worst && worst.p95Ms >= p95) return worst;
      return { step: node.node_name || "—", p95Ms: p95 };
    }, null);

    const nodeCost = group.reduce<number | null>((sum, node) => {
      const cost = num(node.total_cost);
      if (cost === null) return sum;
      return (sum ?? 0) + cost;
    }, null);
    const totalCost = rollup ? rollup.cost : nodeCost;

    const tokensIn = group.reduce(
      (sum, node) => sum + (num(node.input_tokens) ?? 0),
      0,
    );
    const tokensOut = group.reduce(
      (sum, node) => sum + (num(node.output_tokens) ?? 0),
      0,
    );

    return {
      key,
      provider: provider || "—",
      model: model || "Unnamed model",
      answers,
      calls,
      errors,
      freq: 0,
      avgMs: weighted.calls > 0 ? weighted.ms / weighted.calls : null,
      slowest,
      costPerAnswer:
        totalCost !== null && answers > 0 ? totalCost / answers : null,
      totalCost,
      errorFreeRate: calls > 0 ? ((calls - errors) / calls) * 100 : null,
      tokensInPerCall: calls > 0 ? tokensIn / calls : null,
      tokensOutPerCall: calls > 0 ? tokensOut / calls : null,
      steps: group.map((node) => node.node_name || "—"),
    };
  });

  // Share of the models' own total, not of the window's queries: a question
  // answered by two models would otherwise make the column sum past 100%.
  const totalAnswers = rows.reduce((sum, row) => sum + row.answers, 0);
  for (const row of rows) {
    row.freq = totalAnswers > 0 ? (row.answers / totalAnswers) * 100 : 0;
  }

  return rows.sort((a, b) => b.answers - a.answers);
}

export function stepModelRows(
  scorecard: Scorecard | undefined,
  step: string,
): StepModelRow[] {
  const onStep = nodeRows(scorecard).filter(
    (node) => (node.node_name || "") === step,
  );
  const totalRuns = onStep.reduce(
    (sum, node) => sum + (num(node.total_calls) ?? 0),
    0,
  );

  return onStep
    .map((node) => {
      const runs = num(node.total_calls) ?? 0;
      const errors = num(node.errors) ?? 0;
      const cost = num(node.total_cost);
      return {
        key: nodeKey(node),
        provider: node.provider ?? "—",
        model: modelLabel(node),
        runs,
        freq: totalRuns > 0 ? (runs / totalRuns) * 100 : 0,
        avgMs: num(node.avg_latency_ms),
        p50Ms: num(node.p50_latency_ms),
        p95Ms: num(node.p95_latency_ms),
        costPerRun: cost !== null && runs > 0 ? cost / runs : null,
        errorFreeRate: runs > 0 ? ((runs - errors) / runs) * 100 : null,
        tokensIn:
          runs > 0 && num(node.input_tokens) !== null
            ? (num(node.input_tokens) as number) / runs
            : null,
        tokensOut:
          runs > 0 && num(node.output_tokens) !== null
            ? (num(node.output_tokens) as number) / runs
            : null,
      };
    })
    .sort((a, b) => b.runs - a.runs);
}

/** Which model wins each comparison — undefined when nothing is comparable. */
export function superlatives(rows: ModelRow[]) {
  const withLatency = rows.filter((row) => row.avgMs !== null);
  const withCost = rows.filter((row) => row.costPerAnswer !== null);
  const withRate = rows.filter((row) => row.errorFreeRate !== null);
  return {
    fastest: withLatency.sort(
      (a, b) => (a.avgMs as number) - (b.avgMs as number),
    )[0],
    cheapest: withCost.sort(
      (a, b) => (a.costPerAnswer as number) - (b.costPerAnswer as number),
    )[0],
    // Only a winner when the models actually differ: one row, or a tie across
    // all of them, is not "the most reliable model".
    mostReliable:
      new Set(withRate.map((row) => row.errorFreeRate)).size > 1
        ? withRate.sort(
            (a, b) => (b.errorFreeRate as number) - (a.errorFreeRate as number),
          )[0]
        : undefined,
  };
}

/** The one-word read on a model's showing on a step. */
export function stepVerdict(
  row: StepModelRow,
  rows: StepModelRow[],
): { label: string; tone: "success" | "accent" | "warn" | "neutral" } | null {
  if (rows.length < 2) return null;
  const latencies = rows
    .map((entry) => entry.avgMs)
    .filter((value): value is number => value !== null);
  const costs = rows
    .map((entry) => entry.costPerRun)
    .filter((value): value is number => value !== null);

  if (
    row.avgMs !== null &&
    latencies.length > 1 &&
    row.avgMs === Math.min(...latencies)
  ) {
    return { label: "Fastest", tone: "success" };
  }
  if (
    row.costPerRun !== null &&
    costs.length > 1 &&
    row.costPerRun === Math.min(...costs)
  ) {
    return { label: "Cheapest", tone: "accent" };
  }
  if (
    row.avgMs !== null &&
    latencies.length > 1 &&
    row.avgMs === Math.max(...latencies)
  ) {
    return { label: "Slowest", tone: "warn" };
  }
  return null;
}
