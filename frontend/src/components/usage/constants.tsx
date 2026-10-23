import type { TimeBucket } from "@/data/usage";

/**
 * The Usage screen's fixed vocabulary.
 *
 * Colocated here rather than at the top of the component that draws each one,
 * per the repo's `no-restricted-syntax` rule: every value below is one someone
 * will want to change — a provider's name, the wording of a reset window, which
 * metrics the trend offers — without reading JSX to find it.
 */

/**
 * How a provider's key is written in words.
 *
 * The service reports the key; nothing in its answer says how to capitalise it,
 * and "openai" in a sentence about a limit reads as a typo. A key with no entry
 * here falls through as itself rather than being hidden, so a provider added
 * server-side still names itself until it is added here.
 */
export const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  groq: "Groq",
  deepseek: "DeepSeek",
  mistral: "Mistral",
  cohere: "Cohere",
  llamaparse: "LlamaParse",
  firecrawl: "Firecrawl",
  assemblyai: "AssemblyAI",
  aws: "AWS",
};

/**
 * When each kind of allowance starts again.
 *
 * A standing count ("documents you hold") never resets, a daily allowance
 * resets tonight and a monthly one on the 1st; saying "this month" over all of
 * them would misdate three rows out of four.
 */
export const WINDOW_NOTES: Record<string, string> = {
  current: "Counted right now, across the whole account.",
  calendar_day: "Resets at 00:00 UTC.",
  calendar_month: "Resets on the 1st.",
  lifetime: "Counted for the lifetime of this account.",
};

/** The four categorical series, in draw order. Never cycled past the fourth. */
export const SERIES = [
  "var(--di-series-1)",
  "var(--di-series-2)",
  "var(--di-series-3)",
  "var(--di-series-4)",
] as const;

/** What the trend can plot, in the order the control offers them. */
export const TREND_METRICS = [
  { id: "total_tokens" as const, label: "Total tokens" },
  { id: "input_tokens" as const, label: "Input tokens" },
  { id: "output_tokens" as const, label: "Output tokens" },
  { id: "request_count" as const, label: "Requests" },
  { id: "pages" as const, label: "Pages read" },
  { id: "cost" as const, label: "Cost" },
];

export type TrendMetricId = (typeof TREND_METRICS)[number]["id"];

/** The buckets it can plot them in — the service's own `TimeBucket` values. */
export const TREND_GRANULARITIES: Array<{ id: TimeBucket; label: string }> = [
  { id: "day", label: "Daily" },
  { id: "week", label: "Weekly" },
  { id: "month", label: "Monthly" },
];

/**
 * The Usage screen's own rail: what each section is called, what its heading
 * says, and the glyph beside it.
 *
 * The icon travels with the name because the alternative — a list of labels
 * here and a map of pictures somewhere else — is how a section ends up with
 * another section's glyph.
 */
export const SECTIONS = [
  {
    id: "overview" as const,
    label: "Overview",
    title: "Account Overview",
    icon: (
      <svg
        viewBox="0 0 20 20"
        width="15"
        height="15"
        fill="none"
        aria-hidden="true"
      >
        <rect
          x="3"
          y="3"
          width="6"
          height="6"
          rx="1.4"
          stroke="currentColor"
          strokeWidth="1.5"
        />
        <rect
          x="11"
          y="3"
          width="6"
          height="6"
          rx="1.4"
          stroke="currentColor"
          strokeWidth="1.5"
        />
        <rect
          x="3"
          y="11"
          width="6"
          height="6"
          rx="1.4"
          stroke="currentColor"
          strokeWidth="1.5"
        />
        <rect
          x="11"
          y="11"
          width="6"
          height="6"
          rx="1.4"
          stroke="currentColor"
          strokeWidth="1.5"
        />
      </svg>
    ),
  },
  {
    id: "collections" as const,
    label: "Collections",
    title: "Collections",
    icon: (
      <svg
        viewBox="0 0 20 20"
        width="15"
        height="15"
        fill="none"
        aria-hidden="true"
      >
        <path
          d="M6 3h6l3 3v11H6z"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
        <path
          d="M8 9h5M8 12h5"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinecap="round"
        />
      </svg>
    ),
  },
  {
    id: "limits" as const,
    label: "Limits",
    title: "Your Plan",
    icon: (
      <svg
        viewBox="0 0 20 20"
        width="15"
        height="15"
        fill="none"
        aria-hidden="true"
      >
        <path
          d="M3 14.5a7 7 0 1 1 14 0"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
        <path
          d="M10 14.5l3.6-4.6"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
      </svg>
    ),
  },
  {
    id: "filters" as const,
    label: "Custom Filters",
    title: "Custom Filters",
    icon: (
      <svg
        viewBox="0 0 20 20"
        width="15"
        height="15"
        fill="none"
        aria-hidden="true"
      >
        <path
          d="M3 5h14l-5.5 6.2V16l-3 1.5v-6.3z"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
];
