/**
 * Reads the analytics parameter contract out of the NL2SQL service's OpenAPI
 * document.
 *
 * The screen used to carry its own copy of every vocabulary the API accepts —
 * which routes exist, which feedback values, how large a page may be. One of
 * those copies was the agent dashboard's list of service families
 * (`knowledge`, `integration`, …) being sent as NL2SQL's `route`, a parameter
 * whose values are `sql | cross_db | pandas | direct_answer`. Nothing failed
 * loudly; the log simply came back empty for every choice in the dropdown.
 *
 * So the vocabularies are not written here. They are extracted from the
 * service's own spec by `scripts/gen-analytics-contract.ts` and committed as
 * `src/generated/analyticsContract.ts`; this module is the extraction, kept in
 * `src/` rather than in the script so it can be unit-tested.
 *
 * FastAPI emits these enums as prose in `description` rather than as JSON
 * Schema `enum`, so they have to be parsed — carefully, because most
 * descriptions are ordinary sentences and must yield nothing at all.
 */

/** One parameter, as the service describes it. */
export interface ParamContract {
  /** The server-side default, or `null` when the parameter is optional. */
  default: string | number | boolean | null;
  min: number | null;
  max: number | null;
  /** Accepted values, when the spec documents a closed set. */
  values: readonly string[] | null;
}

export type EndpointContract = Record<string, ParamContract>;
export type AnalyticsContract = Record<string, EndpointContract>;

/** A bare identifier — the shape every documented enum value has. */
const IDENTIFIER = /^[a-z][a-z0-9_]*$/;

/**
 * Prose that introduces a list, e.g. `granularity: hour | day` or
 * `csv subset of success_rate,avg_ms`. Cut at the last one so the lead-in never
 * lands in the first value.
 */
const LEAD_IN = /(?::\s|\sof\s)/g;

/**
 * The closed set of values a description documents, or `null` when it documents
 * none.
 *
 * Every token must be a bare identifier for the result to count as a
 * vocabulary. That rule is what keeps ordinary prose out: "Custom range end,
 * exclusive (ISO-8601)" splits on its comma into two candidates, and
 * "Custom range end" is not an identifier, so the whole description is
 * rejected rather than half-read as an enum.
 */
export function parseVocabulary(
  description: string | undefined | null,
): string[] | null {
  if (!description) return null;

  // Parentheticals gloss a value ("none (unrated)", "run_id (direct read_run
  // per row)") and never contain one.
  let text = description.replace(/\([^)]*\)/g, " ");

  let cut = -1;
  for (const match of text.matchAll(LEAD_IN)) {
    cut = match.index + match[0].length;
  }
  if (cut >= 0) text = text.slice(cut);

  const tokens = text
    .split(/[|,]/)
    .map((part) => part.trim())
    .filter(Boolean);

  if (tokens.length < 2) return null;
  if (!tokens.every((token) => IDENTIFIER.test(token))) return null;

  return [...new Set(tokens)];
}

/** The scalar schema of a parameter, past any `anyOf: [T, null]` wrapper. */
function scalarSchema(schema: unknown): Record<string, unknown> {
  const s = (schema ?? {}) as Record<string, unknown>;
  const anyOf = s.anyOf;
  if (!Array.isArray(anyOf)) return s;
  const branch = anyOf.find(
    (b) => (b as Record<string, unknown>)?.type !== "null",
  );
  return { ...s, ...((branch ?? {}) as Record<string, unknown>) };
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function paramContract(param: Record<string, unknown>): ParamContract {
  const schema = scalarSchema(param.schema);
  const fallback = schema.default;
  return {
    default:
      typeof fallback === "string" ||
      typeof fallback === "number" ||
      typeof fallback === "boolean"
        ? fallback
        : null,
    min: numberOrNull(schema.minimum),
    max: numberOrNull(schema.maximum),
    values: parseVocabulary(param.description as string | undefined),
  };
}

/**
 * `/v1/agents/{agent_id}/analytics/sync-costs` → `syncCosts`.
 *
 * Keyed by the endpoint's own last segment rather than by an index, so a
 * regenerated contract that gains or loses a route keeps every existing key
 * pointing at the same endpoint.
 */
export function endpointKey(path: string): string {
  const segment = path.split("/").filter(Boolean).pop() ?? path;
  return segment.replace(/-([a-z])/g, (_, c: string) => c.toUpperCase());
}

/** Every `…/analytics/…` operation in the document, as a parameter contract. */
export function buildAnalyticsContract(spec: unknown): AnalyticsContract {
  const paths = ((spec as Record<string, unknown>)?.paths ?? {}) as Record<
    string,
    Record<string, { parameters?: Record<string, unknown>[] }>
  >;
  const contract: AnalyticsContract = {};

  for (const path of Object.keys(paths).sort()) {
    if (!path.includes("/analytics")) continue;
    for (const operation of Object.values(paths[path])) {
      const endpoint: EndpointContract = {};
      for (const param of operation.parameters ?? []) {
        // Path params are not vocabularies the UI chooses from.
        if (param.in !== "query") continue;
        endpoint[String(param.name)] = paramContract(param);
      }
      contract[endpointKey(path)] = endpoint;
    }
  }

  return contract;
}
