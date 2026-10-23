import type { Page, Route } from "@playwright/test";

/**
 * The knowledge service, stubbed.
 *
 * Document Intelligence calls one service, so a run against these fixtures
 * exercises every request the product makes — and any request aimed at another
 * service is blocked and recorded in `recorder.violations`, which is the product
 * rule these specs exist for. Every body below is the shape the live service
 * answered when this was written; the unit suite checks the same routes against
 * the published contract.
 */

export const OWNER = "e2e@futuresmart.ai";
/** The signed-in account's id — what a session's `user_id` carries. */
export const OWNER_USER_ID = "e2e-user";
/** Another account that has asked this collection something. */
export const OTHER_USER_ID = "colleague-user";
export const KNOWLEDGE_ID = "9e7e5767-5da0-44e2-ab7e-06c0d70b382c";
export const SECOND_KNOWLEDGE_ID = "3ca68247-87f5-4304-900c-33f8ecfa52e4";
/** A knowledge base with no `collection_name` — not a collection here. */
export const UNNAMED_KNOWLEDGE_ID = "2cacb101-2a85-4e4d-b407-2958559ccf85";
export const SESSION_ID = "ac50bcab-b846-48b0-9088-f72b8af98781";
/** A thread another account opened, listed only on the Users tab. */
export const OTHER_SESSION_ID = "b71f2d90-4c33-4a1e-9f0c-2ad4e6c1b7aa";
/**
 * A thread nobody signed in for — the website bubble.
 *
 * `user_id: null` is how the service records a caller that never signed in, and
 * Share → Activity is the screen that reads it as such.
 */
export const WIDGET_SESSION_ID = "c2f4a180-7b31-4d55-9e21-58f0a1c4d903";
/** A thread a master-key integration opened. */
export const API_SESSION_ID = "d41a7f66-2c58-4b0a-8f77-9e0a34bb1122";
/** A thread from before the service recorded an origin — on no Activity tab. */
export const LEGACY_SESSION_ID = "e97b3c21-0f44-4c8e-b2d5-71aa0c9e6f30";
export const CHAT_ID = "3b83a158-65cf-4bbc-8b8a-de64581c2a74";

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

const knowledgeConfig = (id: string, name: string | null) => ({
  knowledge_id: id,
  embedding_model: "paraphrase-multilingual-mpnet-base-v2",
  hybrid_search_enabled: true,
  chunking_strategy: {
    document_level_metadata: true,
    chunk_level_metadata: false,
  },
  collection_name: name,
  service_description: name ? `Everything in ${name}.` : null,
  enrichment_enabled: true,
  use_deterministic_chunking: false,
  created_at: "2026-08-01T10:00:00+00:00",
  updated_at: "2026-08-02T10:00:00+00:00",
});

const chatConfig = {
  knowledge_id: KNOWLEDGE_ID,
  configured: true,
  system_prompt: null,
  llm_provider: "openai",
  llm_model: "gpt-5.6-luna",
  temperature: 0.3,
  max_tokens: 1024,
  show_sources: true,
  chat_enabled: true,
  follow_up: true,
  suggested_questions: ["Who has Python experience?"],
  voice_input_enabled: false,
  voice_output_enabled: false,
  include_charts: true,
  show_raw_response: false,
  // The two hosted surfaces, deliberately in different states: the Share screen
  // reads one switch per channel, and the service treats them as independent.
  is_public: false,
  allowed_domains: [],
  fullscreen_is_public: true,
  fullscreen_allowed_domains: ["acme.com"],
  node_configs: {},
};

const documents = {
  document: [
    {
      id: "1988",
      object_id: "ccb15ed8-a152-4c04-b629-7aae79515298",
      knowledge_id: KNOWLEDGE_ID,
      file_name: "priya-sharma-resume.pdf",
      status: "completed",
      error_detail: null,
      content_type: "file",
      created_time: "2026-08-01T15:00:28.266481",
      updated_time: "2026-08-01T15:00:28.266481",
      source_presigned_url: "https://example.invalid/original.pdf",
      extracted_s3_url: "https://example.invalid/extracted.md",
    },
    {
      id: "1989",
      object_id: "f60479d0-5c00-4b57-b98e-a7fd2e433a69",
      knowledge_id: KNOWLEDGE_ID,
      file_name: "arjun-mehta-resume.pdf",
      status: "processing",
      error_detail: null,
      content_type: "file",
      created_time: "2026-08-01T15:02:28.266481",
      updated_time: "2026-08-01T15:02:28.266481",
      source_presigned_url: null,
      extracted_s3_url: null,
    },
  ],
  total_count: 2,
  current_page: 1,
  total_pages: 1,
};

/**
 * `GET /v1/field-templates` — the kinds of collection on offer, each with the
 * fields that kind of document usually carries, already typed.
 *
 * The create screen renders the picker from this rather than from a constant,
 * so this fixture is what decides which cards appear on step one.
 */
const fieldTemplates = {
  templates: [
    {
      key: "resume",
      display_name: "Resumes / CVs",
      description: "People applying for a role.",
      fields: [
        {
          field_name: "candidate_name",
          description: "Full name of the candidate.",
          field_type: "str",
          field_type_label: "Text",
        },
        {
          field_name: "years_of_experience",
          description: "Total years worked.",
          field_type: "int",
          field_type_label: "Number",
        },
        {
          field_name: "work_history",
          description: "Past jobs; one row per job.",
          field_type: "List[dict]",
          field_type_label: "Table",
          schema: {
            company_name: "str",
            job_title: "str",
            start_date: "date",
          },
        },
      ],
    },
    {
      key: "contract",
      display_name: "Contracts",
      description: "Agreements with another party.",
      fields: [
        {
          field_name: "other_party",
          description: "Who the agreement is with.",
          field_type: "str",
          field_type_label: "Text",
        },
      ],
    },
    { key: "default", display_name: "Something else", fields: [] },
  ],
};

/**
 * `GET /v1/field-types` — the catalogue the type dropdown is built from.
 *
 * The eight the service publishes, in its own words. `int` and `float` really
 * do share the label "Number"; `requires_schema` really is true for exactly
 * `dict` and `List[dict]`.
 */
const fieldTypes = {
  field_types: [
    { field_type: "str", field_type_label: "Text", requires_schema: false },
    {
      field_type: "List[str]",
      field_type_label: "Text (multiple values)",
      requires_schema: false,
    },
    { field_type: "int", field_type_label: "Number", requires_schema: false },
    { field_type: "float", field_type_label: "Number", requires_schema: false },
    { field_type: "date", field_type_label: "Date", requires_schema: false },
    {
      field_type: "bool",
      field_type_label: "Yes / No",
      requires_schema: false,
    },
    {
      field_type: "dict",
      field_type_label: "Group of fields",
      requires_schema: true,
    },
    {
      field_type: "List[dict]",
      field_type_label: "Table",
      requires_schema: true,
    },
  ],
};

/**
 * What `POST /v1/enrichment/generate` answers for a described collection.
 *
 * Unscoped, and it writes nothing — `run.persisted` is `false`. The proposals
 * land on the board as editable rows. `default_field_verdicts` is kept here
 * because the live route answers with it, but the create screen ignores it:
 * the optional defaults are neither shown nor created.
 */
const generatedSchema = {
  proposed_fields: [
    {
      field_name: "other_party",
      description: "Who the agreement is with.",
      field_type: "str",
      field_type_label: "Text",
    },
    {
      field_name: "expires_on",
      description: "When the agreement ends.",
      field_type: "date",
      field_type_label: "Date",
    },
    {
      field_name: "contract_value",
      description: "The total value of the agreement.",
      field_type: "float",
      field_type_label: "Number",
    },
  ],
  default_field_verdicts: [
    { field_name: "topics", keep: true, reason: "Useful for grouping." },
    { field_name: "entities", keep: false, reason: "Rarely in a contract." },
  ],
  rejected_fields: [{ field_name: "salary", reason: "Not in the documents." }],
  run: { persisted: false },
};

/** What `POST /v1/enrichment/preview` answers for one sample document. */
const samplePreview = {
  extracted: { candidate_name: "Ananya Rao", years_of_experience: 7 },
  fields_run: [
    { field_name: "candidate_name", status: "extracted" },
    { field_name: "years_of_experience", status: "extracted" },
    { field_name: "work_history", status: "not_found" },
  ],
  suggested_fields: [
    {
      field_name: "linkedin_url",
      description: "Profile link on the document.",
      field_type: "str",
      reason: "Present on the document but not requested",
    },
  ],
  document: { file_name: "ananya.pdf", text_truncated: false },
  run: { persisted: false },
};

/**
 * The fields the stubbed collection carries.
 *
 * Exported because a demo stages this route with a memory — it keeps a field it
 * is POSTed and answers later reads with this list plus that one — and reading
 * the base list from here is what stops the two copies drifting.
 */
export const enrichment = {
  fields: [
    {
      id: 4688,
      knowledge_id: KNOWLEDGE_ID,
      field_name: "candidate_name",
      description: "The candidate's full name",
      field_type: "str",
      // The service returns the type in words on every field now, and this
      // product shows that rather than the wire type.
      field_type_label: "Text",
      level: "document",
      is_custom: true,
      is_required: false,
      is_active: true,
      // The rest of the row exactly as the service answers with it — every one
      // of these keys is on a live field, and a fixture missing them is a
      // fixture the form cannot save (measured 2026-09-16).
      sample_values: [],
      restrict_to_choices: false,
      max_items: null,
      schema: null,
      metadata: [],
    },
    {
      id: 4689,
      knowledge_id: KNOWLEDGE_ID,
      field_name: "document_type",
      description: "What kind of document this is",
      // No label: a row saved before the service returned one, which the screen
      // still has to show in words.
      field_type: "List[dict]",
      level: "document",
      is_custom: false,
      is_required: true,
      is_active: true,
      sample_values: [],
      restrict_to_choices: false,
      max_items: null,
      schema: null,
      metadata: [],
    },
  ],
};

/**
 * Two accounts' threads, so the two tabs are distinguishable: My Conversations
 * asks with `?user_id=` and gets one, Users asks without and gets both.
 */
const allSessions = [
  {
    session_id: SESSION_ID,
    title: "Who has Python experience?",
    exchange_count: 1,
    user_id: OWNER_USER_ID,
    user_email: OWNER,
    user_name: "E2E User",
    source: "dashboard",
    created_at: "2026-08-18T18:14:51.376203",
    updated_at: "2026-08-18T18:15:10.000000",
  },
  {
    session_id: OTHER_SESSION_ID,
    title: "Which candidates know Kubernetes?",
    exchange_count: 2,
    user_id: OTHER_USER_ID,
    user_email: "priya.nair@futuresmart.ai",
    user_name: "Priya Nair",
    source: "dashboard",
    created_at: "2026-08-17T09:02:11.100000",
    updated_at: "2026-08-17T09:40:00.000000",
  },
  {
    session_id: WIDGET_SESSION_ID,
    title: "How many candidates have Go experience?",
    exchange_count: 3,
    user_id: null,
    user_email: null,
    user_name: null,
    source: "widget",
    created_at: "2026-08-16T11:41:02.000000",
    updated_at: "2026-08-16T12:02:00.000000",
  },
  {
    session_id: API_SESSION_ID,
    title: "List every candidate with a notice period under 30 days",
    exchange_count: 5,
    user_id: null,
    user_email: null,
    user_name: null,
    source: "api",
    created_at: "2026-08-15T08:00:00.000000",
    updated_at: "2026-08-15T08:30:00.000000",
  },
  {
    // Recorded before migration 044: no origin, so ?source= matches it never
    // and it is listed only when the filter is omitted. The screen counts
    // these and says so rather than showing a short list.
    session_id: LEGACY_SESSION_ID,
    title: "An older question nobody labelled",
    exchange_count: 1,
    user_id: OTHER_USER_ID,
    user_email: null,
    user_name: null,
    source: null,
    created_at: "2026-07-02T10:00:00.000000",
    updated_at: null,
  },
];

const sessionPage = (rows: typeof allSessions) => ({
  sessions: rows,
  total_count: rows.length,
  current_page: 1,
  total_pages: 1,
});

/**
 * Usage, for the account.
 *
 * The service scopes this surface to the owner, not to a knowledge base, so
 * there is one answer here rather than one per collection — a screen that went
 * back to fanning out over collections would have no route to call.
 */
const usageStatus = {
  owner_id: OWNER,
  period: {
    start: "2026-08-01T00:00:00+00:00",
    end: "2026-09-01T00:00:00+00:00",
  },
  limits: {
    max_agents: null,
    max_indexed_characters: 1_000_000,
    max_conversations_per_month: null,
    max_messages_per_month: null,
    max_llm_tokens_per_month: 500_000,
    max_documents: 500,
    max_urls: null,
    max_messages_per_day: 100,
    max_cost_usd_per_month: 25,
  },
  current_usage: {
    indexed_characters: 300,
    conversations_this_month: 3,
    messages_this_month: 12,
    llm_tokens_this_month: 3_000,
    documents: 9,
    urls: 2,
    messages_today: 4,
    cost_usd_this_month: 3.8,
  },
  remaining: {
    indexed_characters: 999_700,
    conversations: null,
    messages: null,
    llm_tokens: 497_000,
    documents: 491,
    urls: null,
    messages_today: 96,
    cost_usd: 21.2,
  },
};

/**
 * The Limits tab, already resolved into rows by the service.
 *
 * Every judgement the tab draws is in the answer — `percent`, `lifted`,
 * `enforced` and the wording — so the fixture carries the three shapes that
 * are rendered differently: a metered row, an uncapped one awaiting its
 * backfill, and a display-only row that never refuses anything.
 */
const limitsScreen = {
  owner_id: OWNER,
  tier: { name: "Trial", tier_type: "trial", is_default: true },
  window: {
    start: "2026-08-01T00:00:00+00:00",
    end: "2026-09-01T00:00:00+00:00",
  },
  own_credentials: ["openai"],
  exceeded: [],
  groups: [
    {
      group: "Workspace",
      items: [
        {
          key: "max_documents",
          label: "Documents you can hold",
          used: 9,
          limit: 500,
          unit: "documents",
          maximum: "500 documents on Trial",
          note: "A sub-cap of the combined workspace item limit.",
          window: "current",
          lifted: false,
          percent: 1.8,
          enforced: true,
        },
        {
          key: "max_urls",
          label: "Links you can hold",
          used: 2,
          limit: null,
          unit: "links",
          maximum: "No limit on this account",
          note: "One row per link added, not per page crawled.",
          window: "current",
          lifted: false,
          percent: null,
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
          used: 4,
          limit: 100,
          unit: "questions",
          maximum: "100 questions a day on Trial",
          note: "Counts every question asked of any collection.",
          window: "calendar_day",
          lifted: false,
          percent: 4,
          enforced: true,
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

/**
 * The catalogue behind the filter pickers.
 *
 * `operations` is a list of objects here because that is what the service
 * answers — `ServiceTypeOption.operations` is `OperationOption[]`, while the
 * `all_operations` beside it is a flat list of strings. A fixture that
 * simplified the first to strings would hide a picker rendering
 * `[object Object]`.
 */
const serviceOptions = {
  service_types: [
    {
      service_type: "knowledge",
      display_name: "Knowledge",
      description: "Reading and indexing documents",
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
  all_labels: [
    "file_processing",
    "rag_agent",
    "content_embedding",
    "generate_answer",
  ],
};

/**
 * The analytics answer, shaped by whatever `group_by` was asked for.
 *
 * The two collections spend different amounts, so the per-collection tab
 * showing one row, or the same figure twice, is visible rather than plausible.
 */
const analyticsResponse = (body: {
  group_by?: string[];
  time_bucket?: string;
  date_range?: { start: string; end: string };
}) => {
  const groupBy = body.group_by ?? [];
  const point = (
    dimensions: Record<string, string>,
    requests: number,
    pages: number,
    cost: number,
  ) => ({
    dimensions,
    metrics: {
      request_count: requests,
      total_tokens: requests * 100,
      llama_parse_page_count: pages,
      processed_weblink_count: 0,
      cost: { total_cost_usd: cost },
    },
  });

  let data: ReturnType<typeof point>[] = [];
  if (groupBy.includes("knowledge_id")) {
    data = [
      point({ knowledge_id: KNOWLEDGE_ID }, 8, 40, 0.8),
      point({ knowledge_id: SECOND_KNOWLEDGE_ID }, 4, 20, 0.4),
    ];
  } else if (groupBy.includes("operation")) {
    data = [
      point({ operation: "llm_generation" }, 9, 0, 0.9),
      point({ operation: "data_ingestion" }, 3, 60, 0.3),
    ];
  } else if (body.time_bucket) {
    data = [
      point({ time_bucket: "2026-08-18" }, 4, 20, 0.4),
      point({ time_bucket: "2026-08-19" }, 3, 15, 0.3),
      point({ time_bucket: "2026-08-20" }, 5, 25, 0.5),
    ];
  }

  return {
    owner_id: OWNER,
    executed_at: "2026-08-20T12:00:00+00:00",
    date_range: body.date_range ?? { start: "2026-07-21", end: "2026-08-20" },
    group_by: groupBy,
    time_bucket: body.time_bucket ?? null,
    totals: {
      request_count: 12,
      total_tokens: 1_200,
      llama_parse_page_count: 60,
      processed_weblink_count: 0,
      cost: { total_cost_usd: 1.2 },
    },
    knowledge_overview_metrics: {
      total_files_processed: 24,
      total_char_count: 12_000,
      total_audio_seconds: 0,
      total_llama_parse_pages: 60,
      total_weblink_pages: 0,
      total_text_count: 0,
      total_embedding_tokens: 900,
      total_vectors_indexed: 300,
      cost: { total_cost_usd: 1.2 },
    },
    data,
    total_groups: data.length,
    limit: 1000,
    offset: 0,
    has_more: false,
  };
};

/** One stored key, masked — the service never returns the key itself. */
const credentials = [
  {
    provider: "openai",
    mask: "sk-…4f2a",
    is_valid: true,
    updated_at: "2026-08-18T18:14:51.376203",
  },
];

const session = {
  session_id: SESSION_ID,
  knowledge_id: KNOWLEDGE_ID,
  user_id: "e2e-user",
  title: "Who has Python experience?",
  created_at: "2026-08-18T18:14:51.376203",
  updated_at: null,
  exchange_count: 1,
  messages: [
    {
      n: 1,
      chat_id: CHAT_ID,
      role: "user",
      message: "Who has Python experience?",
      created_at: "2026-08-18T18:14:51.394443",
      user_feedback: null,
      data: {},
    },
    {
      n: 1,
      chat_id: CHAT_ID,
      role: "assistant",
      message: "Priya Sharma lists six years of Python.",
      created_at: "2026-08-18T18:14:52.394443",
      user_feedback: null,
      data: {
        knowledge: {
          relevant_sources: {
            data: [
              {
                id: "ccb15ed8-a152-4c04-b629-7aae79515298",
                title: "priya-sharma-resume.pdf",
                context:
                  "Six years of Python, three of them on data pipelines.",
              },
            ],
            tool_name: "rag",
          },
        },
      },
    },
  ],
};

/** The events `POST …/chat` streams, in the order the live service sends them. */
const chatStream = [
  `data: ${JSON.stringify({ chunk: "Priya Sharma lists six years of Python." })}\n\n`,
  `data: ${JSON.stringify({
    query: "Who has Python experience?",
    refined_query: "Who has Python experience?",
    response: "Priya Sharma lists six years of Python.",
    session_id: SESSION_ID,
    chat_id: CHAT_ID,
    follow_up: ["Who else knows Python?"],
    data: session.messages[1].data,
  })}\n\n`,
].join("");

/** The requests each spec can assert against. */
export interface Recorder {
  urls: string[];
  /** `METHOD path` for every request that reached a stub. */
  calls: string[];
  bodies: Record<string, unknown[]>;
  /**
   * `METHOD path` for every call to a service other than the knowledge one —
   * the product rule under test. Each is blocked as well as recorded: a spec
   * that finds one has already failed, and no test run should reach a real
   * backend.
   */
  violations: string[];
}

/**
 * Stubs the session and the whole knowledge service, and blocks every call to
 * any other service.
 *
 * One handler covers all of `**\/api/**` rather than two, because Playwright
 * uses a single matching route per request: a second, broader handler would
 * either shadow these stubs or never see anything, depending on registration
 * order.
 */
/* ── batch reports ──────────────────────────────────────────────────────── */

export const REPORT_ID = "rep-7f21";
export const RUN_ID = "run-1a2b";

const report = {
  report_id: REPORT_ID,
  knowledge_id: KNOWLEDGE_ID,
  user_id: OWNER_USER_ID,
  name: "Weekly candidate pool review",
  report_instructions: "Answer from the resumes only.",
  analysis_prompt: "Summarise the pool in three lines.",
  query_groups: [
    {
      type: "independent",
      queries: ["Who has Python experience?", "Who can start immediately?"],
    },
    { type: "sequential", queries: ["Of those, who has led a team?"] },
  ],
  schedule: { frequency: "cron", cron: "0 9 * * 1", timezone: "UTC" },
  next_run_at: "2026-08-24T09:00:00+00:00",
  created_at: "2026-08-10T09:00:00+00:00",
  updated_at: "2026-08-18T09:00:00+00:00",
};

const runListItem = {
  run_id: RUN_ID,
  report_id: REPORT_ID,
  status: "completed",
  trigger: "manual",
  progress: {
    total_queries: 3,
    completed_queries: 3,
    failed_queries: 0,
    percent_complete: 100,
  },
  started_at: "2026-08-19T09:00:03+00:00",
  ended_at: "2026-08-19T09:01:11+00:00",
  created_at: "2026-08-19T09:00:00+00:00",
  total_time_ms: 68000,
};

const runResult = {
  run_id: RUN_ID,
  report_id: REPORT_ID,
  name: report.name,
  knowledge_id: KNOWLEDGE_ID,
  status: "completed",
  trigger: "manual",
  executed_at: "2026-08-19T09:00:03+00:00",
  started_at: "2026-08-19T09:00:03+00:00",
  ended_at: "2026-08-19T09:01:11+00:00",
  failure_reason: null,
  summary: {
    total_queries: 3,
    completed_queries: 2,
    failed_queries: 1,
    avg_response_time_ms: 18400,
    total_tokens: 2552,
    total_time_ms: 68000,
  },
  overall_summary: "Two of the three questions were answerable.",
  query_groups: [
    {
      group_id: "g1",
      type: "independent",
      results: [
        {
          question: "Who has Python experience?",
          query_id: "q1",
          group_id: "g1",
          answer: "**Priya Sharma** has six years of Python.",
          sources_used: [{ file_name: "priya-sharma-resume.pdf" }],
          retrieved_documents: [],
          retrieved_chunks: [
            { chunk_id: "c1", content: "Python, Django, six years." },
          ],
          relevance_score: 0.91,
          response_time_ms: 17400,
          token_usage: 604,
          error: null,
        },
        {
          question: "Who can start immediately?",
          query_id: "q2",
          group_id: "g1",
          answer: null,
          sources_used: [],
          retrieved_documents: [],
          retrieved_chunks: [],
          relevance_score: null,
          response_time_ms: 900,
          error: "No availability is recorded in these documents.",
        },
      ],
    },
  ],
};

/* ── sharing ────────────────────────────────────────────────────────────── */

export const INVITED_EMAIL = "colleague@futuresmart.ai";
export const API_KEY_PREFIX = "fsk_live_a1b2";

const invitations = {
  invited_users: [
    {
      email: INVITED_EMAIL,
      role: "viewer",
      invited_at: "2026-08-12T10:00:00+00:00",
    },
  ],
};

const widgetConfig = {
  widget_settings: {
    heading: "Ask the resumes",
    sub_heading: "Answers come with their sources.",
    greet_message: "Hello! Ask me about these candidates.",
    color: "#0f9488",
    show_sources: true,
  },
};

const apiKeys = {
  api_keys: [
    {
      key_prefix: API_KEY_PREFIX,
      knowledge_id: KNOWLEDGE_ID,
      is_active: true,
      created_at: "2026-08-15T10:00:00+00:00",
      revoked_at: null,
    },
    // A key for another collection: it must not be listed on this one.
    {
      key_prefix: "fsk_live_zzzz",
      knowledge_id: SECOND_KNOWLEDGE_ID,
      is_active: true,
      created_at: "2026-08-16T10:00:00+00:00",
      revoked_at: null,
    },
  ],
};

/* ── analytics (the `/health/*` namespace) ──────────────────────────────── */

const scorecard = {
  knowledge_id: KNOWLEDGE_ID,
  period: "week",
  since: "2026-08-16T06:36:23+00:00",
  until: "2026-08-23T06:36:23+00:00",
  previous_since: "2026-08-09T06:36:23+00:00",
  usage: {
    total_queries: 5,
    unique_users: 1,
    widget_queries: 0,
    bucket: "day",
    per_day: [
      { bucket: "2026-08-19T00:00:00+00:00", day: "2026-08-19", queries: 5 },
    ],
  },
  quality: {
    answered: 3,
    no_answer: 2,
    error: 0,
    success_rate: 60.0,
    feedback: { positive: 1, negative: 0, total: 1 },
  },
  perf: {
    avg_ms: 19783.4,
    p50_ms: 21217.0,
    p95_ms: 22819.2,
    // One row per (step, model), which is what the service reports and what
    // the Model performance tab is built on.
    by_node: [
      {
        node_name: "understand_question",
        provider: "openai",
        model: "gpt-5.6-terra",
        queries: 4,
        total_calls: 4,
        errors: 0,
        avg_latency_ms: 196,
        p50_latency_ms: 161,
        p95_latency_ms: 431,
        avg_latency_s: 0.196,
        p50_latency_s: 0.161,
        p95_latency_s: 0.431,
        total_tokens: 970,
        input_tokens: 900,
        output_tokens: 70,
        total_cost: 0.00028,
      },
      {
        node_name: "compose_answer",
        provider: "openai",
        model: "gpt-5.6-terra",
        queries: 4,
        total_calls: 4,
        errors: 1,
        avg_latency_ms: 3050,
        p50_latency_ms: 2440,
        p95_latency_ms: 7015,
        avg_latency_s: 3.05,
        p50_latency_s: 2.44,
        p95_latency_s: 7.015,
        total_tokens: 1400,
        input_tokens: 1200,
        output_tokens: 200,
        total_cost: 0.010625,
      },
      {
        node_name: "understand_question",
        provider: "anthropic",
        model: "claude-haiku",
        queries: 1,
        total_calls: 1,
        errors: 0,
        avg_latency_ms: 90,
        p50_latency_ms: 88,
        p95_latency_ms: 120,
        avg_latency_s: 0.09,
        p50_latency_s: 0.088,
        p95_latency_s: 0.12,
        total_tokens: 200,
        input_tokens: 180,
        output_tokens: 20,
        total_cost: 0.00002,
      },
    ],
    slowest: [
      {
        chat_id: CHAT_ID,
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
      {
        provider: "openai",
        model: "gpt-5.6-terra",
        queries: 4,
        cost: 0.010905,
      },
      {
        provider: "anthropic",
        model: "claude-haiku",
        queries: 1,
        cost: 0.00002,
      },
    ],
    by_node: [],
    most_expensive: [
      {
        chat_id: CHAT_ID,
        query: "can you tell which college all candidates are?",
        total_tokens: 682,
        cost_usd: 0.003967,
        trace_id: "t2",
      },
    ],
  },
  trend: {
    prev_success_rate: 50,
    success_rate_delta: 10,
    prev_avg_ms: 21000,
    avg_ms_delta: -1200,
    prev_total_cost: 0.009,
    cost_delta: 0.0019,
    direction: "up",
  },
};

const trends = {
  knowledge_id: KNOWLEDGE_ID,
  bucket: "day",
  buckets: 7,
  since: "2026-08-16T06:36:30+00:00",
  until: "2026-08-23T06:36:30+00:00",
  metrics: ["success_rate", "avg_ms", "p95_ms", "cost", "failures"],
  series: [
    {
      bucket: "2026-08-19",
      success_rate: 60.0,
      avg_ms: 19783.4,
      p95_ms: null,
      cost: 0.010905,
      failures: 2,
      total_queries: 5,
    },
  ],
  regression: null,
  comparison: { previous_since: "2026-08-09T06:36:30+00:00" },
};

const healthQueryRow = {
  chat_id: CHAT_ID,
  session_id: SESSION_ID,
  user_id: OWNER_USER_ID,
  created_at: "2026-08-19T21:19:03+00:00",
  query: "show me all added document list",
  refined_query: "What is the list of all added documents?",
  route: "knowledge",
  services_used: ["knowledge"],
  status: "no_answer",
  outcome: "failure",
  is_answered: false,
  confidence: null,
  stop_reason: null,
  failure_category: "no_relevant_documents",
  failed_stage: "retrieval",
  failure_detail: null,
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
  cost_breakdown: [],
  node_data: [],
  trace_id: "t1",
};

const failures = {
  knowledge_id: KNOWLEDGE_ID,
  period: "week",
  since: "2026-08-16T06:36:23+00:00",
  until: "2026-08-23T06:36:23+00:00",
  total_failures: 2,
  filters: {},
  by_category: [
    { failure_category: "no_relevant_documents", count: 2, pct: 100 },
  ],
  by_stage: [{ failed_stage: "retrieval", count: 2 }],
  limit: 50,
  offset: 0,
  returned: 1,
  top_failed_queries: [
    {
      query_norm: "show me all added document list",
      failure_category: "no_relevant_documents",
      failed_stage: "retrieval",
      occurrences: 2,
      last_seen: "2026-08-19T21:19:03+00:00",
      first_seen: "2026-08-18T11:02:00+00:00",
      latest_chat_id: CHAT_ID,
      latest_trace_id: "t1",
      sample_failure_detail: null,
    },
  ],
};

export async function mockKnowledgeService(
  page: Page,
  options: {
    collections?: string[];
    chatConfigOverrides?: Record<string, unknown>;
  } = {},
): Promise<Recorder> {
  const recorder: Recorder = {
    urls: [],
    calls: [],
    bodies: {},
    violations: [],
  };
  const ids = options.collections ?? [
    KNOWLEDGE_ID,
    SECOND_KNOWLEDGE_ID,
    UNNAMED_KNOWLEDGE_ID,
  ];

  /**
   * The path from `/api/` on, so an assertion reads the same whether the app is
   * talking to the service directly or through the local dev proxy, which serves
   * it under `/agent`.
   */
  const apiPath = (rawUrl: string) => {
    const { pathname } = new URL(rawUrl);
    const start = pathname.indexOf("/api/");
    return start === -1 ? pathname : pathname.slice(start);
  };

  const record = (route: Route) => {
    const request = route.request();
    const key = `${request.method()} ${apiPath(request.url())}`;
    recorder.urls.push(request.url());
    recorder.calls.push(key);
    const body = request.postData();
    if (body) {
      recorder.bodies[key] = [
        ...(recorder.bodies[key] ?? []),
        (() => {
          try {
            return JSON.parse(body);
          } catch {
            return body;
          }
        })(),
      ];
    }
  };

  // The session. Auth is the one thing the knowledge service does not publish,
  // so it is the one other origin this product talks to.
  // The presigned PUT a sample document goes to. Not this service and not
  // this app's axios — it is S3, signed over its headers — so it gets a route
  // of its own rather than falling through to the API handler.
  await page.route("https://s3.test/**", (route) =>
    route.fulfill({ status: 200, body: "" }),
  );

  await page.route("**/user/me", (route) => {
    record(route);
    return json(route, {
      email: OWNER,
      user_id: OWNER_USER_ID,
      name: "E2E User",
      is_authenticated: true,
    });
  });

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const called = apiPath(request.url());

    if (!called.startsWith("/api/knowledge/")) {
      recorder.violations.push(`${request.method()} ${called}`);
      return route.abort("failed");
    }

    record(route);
    const path = called.replace("/api/knowledge", "");
    const method = request.method();

    // Collections
    if (path === "/v1" && method === "GET") {
      return json(route, {
        owner_id: url.searchParams.get("owner_id"),
        knowledge_services: ids,
        count: ids.length,
      });
    }
    if (path === "/v1" && method === "POST") {
      return json(route, {
        status: "success",
        knowledge_id: "new-collection-id",
        message: "Knowledge service created successfully",
      });
    }

    const configMatch = path.match(/^\/v1\/([^/]+)\/config$/);
    if (configMatch && method === "GET") {
      const id = configMatch[1];
      if (id === UNNAMED_KNOWLEDGE_ID) {
        return json(route, knowledgeConfig(id, null));
      }
      if (id === SECOND_KNOWLEDGE_ID) {
        return json(route, knowledgeConfig(id, "Invoices"));
      }
      return json(route, knowledgeConfig(id, "Resumes"));
    }
    if (configMatch && method === "PUT") {
      return json(route, {
        knowledge_id: configMatch[1],
        message: "Configuration saved successfully",
      });
    }

    // Answering
    if (/^\/v1\/[^/]+\/chat\/config$/.test(path)) {
      if (method === "PUT") {
        return json(route, {
          knowledge_id: KNOWLEDGE_ID,
          message: "Chat configuration saved successfully",
        });
      }
      return json(route, { ...chatConfig, ...options.chatConfigOverrides });
    }
    if (path === "/v1/chat-models") {
      return json(route, {
        providers: [
          {
            provider_name: "openai",
            models: [{ model_name: "gpt-5.6-luna" }, { model_name: "gpt-4o" }],
            default_model: "gpt-5.6-luna",
          },
        ],
      });
    }
    if (/^\/v1\/[^/]+\/chat$/.test(path) && method === "POST") {
      return route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body: chatStream,
      });
    }

    // Usage and the keys it is spent through — owner-scoped, not per
    // collection. A request to the old `/v1/{knowledge_id}/usage/…` shape
    // falls through to the 404 below, which is what the live service does.
    const usageMatch = path.match(/^\/v1\/owners\/[^/]+\/usage\/(.+)$/);
    if (usageMatch) {
      const tail = usageMatch[1];
      if (tail === "limits/status") return json(route, usageStatus);
      if (tail === "limits/screen") return json(route, limitsScreen);
      if (tail === "analytics/service-options") {
        return json(route, serviceOptions);
      }
      if (tail === "analytics/query" && method === "POST") {
        return json(route, analyticsResponse(route.request().postDataJSON()));
      }
      if (tail === "credentials/providers") {
        return json(route, {
          llm_providers: ["openai", "anthropic", "groq", "google"],
          document_and_web_providers: ["llamaparse", "firecrawl"],
        });
      }
      if (tail === "credentials") {
        if (method === "POST") {
          return json(route, {
            status: "success",
            data: {
              provider: "anthropic",
              mask: "sk-…9zz1",
              is_valid: true,
              updated_at: "2026-08-20T00:00:00",
            },
          });
        }
        return json(route, { data: credentials });
      }
      if (tail.startsWith("credentials/") && method === "DELETE") {
        return json(route, {
          status: "success",
          message: "Credential removed",
        });
      }
    }

    // Conversations
    if (/^\/v1\/[^/]+\/sessions$/.test(path)) {
      // Every narrowing this route does in SQL is answered here, so a spec can
      // prove the screen sends the filter rather than splitting a page itself.
      // `source=shared` is the derived one: a dashboard conversation held by
      // somebody other than the owner. A row with no `source` matches no
      // filter at all, exactly as the service documents.
      const userId = url.searchParams.get("user_id");
      const query = url.searchParams.get("q")?.toLowerCase();
      const source = url.searchParams.get("source");
      const exclude = url.searchParams.get("exclude_user_id")?.toLowerCase();
      const sort = url.searchParams.get("sort");
      const order = url.searchParams.get("order") === "asc" ? 1 : -1;

      let rows = allSessions
        .filter((row) => !userId || row.user_id === userId)
        .filter(
          (row) => !query || (row.title ?? "").toLowerCase().includes(query),
        )
        .filter((row) => {
          if (!source) return true;
          if (source === "shared") {
            return row.source === "dashboard" && row.user_id !== OWNER_USER_ID;
          }
          return row.source === source;
        })
        .filter(
          (row) =>
            !exclude ||
            ![row.user_id, row.user_email].some(
              (value) => value?.toLowerCase() === exclude,
            ),
        );

      const key = (row: (typeof allSessions)[number]) => {
        if (sort === "questions") return row.exchange_count;
        if (sort === "title") return (row.title ?? "").toLowerCase();
        if (sort === "user")
          return (row.user_email ?? row.user_id ?? "").toLowerCase();
        return row.updated_at ?? row.created_at ?? "";
      };
      rows = [...rows].sort((a, b) => {
        const x = key(a);
        const y = key(b);
        if (x < y) return -order;
        if (x > y) return order;
        return 0;
      });

      const size = Number(url.searchParams.get("page_size") ?? 20);
      const page = Number(url.searchParams.get("page") ?? 1);
      return json(route, {
        ...sessionPage(rows.slice((page - 1) * size, page * size)),
        total_count: rows.length,
        current_page: page,
        total_pages: Math.max(1, Math.ceil(rows.length / size)),
      });
    }
    if (/^\/v1\/[^/]+\/sessions\/[^/]+$/.test(path)) {
      if (method === "DELETE") {
        return json(route, {
          session_id: SESSION_ID,
          message: "Session deleted",
        });
      }
      return json(route, session);
    }
    if (/\/messages\/[^/]+\/feedback$/.test(path)) {
      return json(route, {
        chat_id: CHAT_ID,
        feedback: "positive",
        message: "Feedback recorded",
      });
    }

    // Documents and fields
    // The passages one document was split into — what the document screen puts
    // beside the values, and the only place the extracted text is readable
    // without downloading the file.
    const chunksMatch = path.match(/^\/v1\/[^/]+\/documents\/([^/]+)\/chunks$/);
    if (chunksMatch) {
      const chunks = [
        {
          id: `${chunksMatch[1]}-c1`,
          text: "PROFESSIONAL SUMMARY\n\nSenior engineer with seven years building high-throughput services.",
        },
        {
          id: `${chunksMatch[1]}-c2`,
          text: "CONTACT DETAILS\n\nAvailability: Immediate. Location: Bengaluru, IN.",
        },
      ];
      return json(route, {
        document_id: chunksMatch[1],
        chunks,
        total_chunks: chunks.length,
        limit: 50,
        offset: 0,
        has_more: false,
      });
    }
    if (/^\/v2\/[^/]+\/documents$/.test(path)) return json(route, documents);
    if (/^\/v1\/[^/]+\/documents$/.test(path)) {
      if (method === "POST") {
        return json(route, {
          status: "processing",
          object_id: "new-object-id",
        });
      }
      return json(route, documents);
    }
    if (/^\/v2?\/[^/]+\/documents\/metadata\/all$/.test(path)) {
      return json(route, {
        columns: [
          {
            name: "candidate_name",
            original_field: "candidate_name",
            field_path: "candidate_name",
            subcolumn: null,
            parent_type: "scalar",
            data_type: "str",
            is_user_selected: true,
          },
        ],
        rows: [{ candidate_name: "Priya Sharma" }],
        total_count: 1,
        current_count: 1,
        current_page: 1,
        total_pages: 1,
      });
    }
    // Batch reports
    const reportsMatch = path.match(/^\/v1\/[^/]+\/reports(\/.*)?$/);
    if (reportsMatch) {
      const tail = reportsMatch[1] ?? "";
      if (tail === "" && method === "GET") {
        return json(route, { reports: [report] });
      }
      if (tail === "" && method === "POST") {
        return json(route, { report_id: "rep-new" }, 201);
      }
      if (tail === "/settings") {
        return json(route, {
          max_queries_per_report: 20,
          default_max_queries_per_report: 20,
        });
      }
      if (/^\/[^/]+\/runs$/.test(tail)) {
        if (method === "POST") {
          return json(route, { run_id: RUN_ID, status: "pending" }, 202);
        }
        return json(route, { runs: [runListItem] });
      }
      if (/^\/[^/]+\/runs\/[^/]+\/status$/.test(tail)) {
        return json(route, {
          run_id: RUN_ID,
          report_id: REPORT_ID,
          knowledge_id: KNOWLEDGE_ID,
          status: "completed",
          progress: runListItem.progress,
          started_at: runListItem.started_at,
          ended_at: runListItem.ended_at,
        });
      }
      if (/^\/[^/]+\/runs\/[^/]+\/export$/.test(tail)) {
        return route.fulfill({
          status: 200,
          contentType: "text/csv",
          body: "question,answer\n",
        });
      }
      if (/^\/[^/]+\/runs\/[^/]+$/.test(tail)) return json(route, runResult);
      if (/^\/[^/]+$/.test(tail) && method === "DELETE") {
        return json(route, { message: "Report deleted" });
      }
      if (/^\/[^/]+$/.test(tail)) return json(route, report);
    }

    // Sharing: who is invited, how it looks, and the keys
    if (/^\/v1\/[^/]+\/invitations$/.test(path)) {
      if (method === "POST") {
        return json(route, { invited_emails: [INVITED_EMAIL] }, 201);
      }
      return json(route, invitations);
    }
    if (/^\/v1\/[^/]+\/invitations\/[^/]+$/.test(path)) {
      return json(route, { message: "Invitation updated", role: "editor" });
    }
    if (/^\/v1\/[^/]+\/widget-config$/.test(path)) {
      if (method === "POST") {
        return json(route, { message: "Added widget settings successfully" });
      }
      if (method === "DELETE") {
        return json(route, { message: "Widget settings removed" });
      }
      return json(route, widgetConfig);
    }
    // Two default assets, served the way the real bucket serves them: the
    // wrong media type, which is why the picker has to recover them.
    if (path === "/v1/widget-config/default-icons") {
      return json(route, {
        widget_icons: [
          { url: "https://assets.invalid/icons/chat.svg" },
          { url: "https://assets.invalid/icons/spark.svg" },
        ],
      });
    }
    if (path === "/v1/widget-config/default-avatars") {
      return json(route, {
        widget_avatars: [{ url: "https://assets.invalid/avatars/bot.svg" }],
      });
    }
    if (path === "/v1/api-keys") {
      if (method === "POST") {
        return json(route, {
          api_key: "fsk_live_a1b2_secret_shown_once",
          key_prefix: API_KEY_PREFIX,
          created_at: "2026-08-23T10:00:00+00:00",
          user_id: OWNER_USER_ID,
        });
      }
      return json(route, apiKeys);
    }
    if (path === "/v1/api-keys/revoke") {
      return json(route, { message: "Key revoked" });
    }

    // Analytics
    if (/^\/v1\/[^/]+\/health\/scorecard$/.test(path)) {
      return json(route, scorecard);
    }
    if (/^\/v1\/[^/]+\/health\/trends$/.test(path)) {
      return json(route, trends);
    }
    if (/^\/v1\/[^/]+\/health\/queries$/.test(path)) {
      const status = url.searchParams.get("status");
      // The live route filters on `status`; answering that here is what lets a
      // spec prove the dropdown sends it rather than filtering in the browser.
      const rows =
        status && status !== healthQueryRow.status ? [] : [healthQueryRow];
      return json(route, {
        knowledge_id: KNOWLEDGE_ID,
        period: url.searchParams.get("period") ?? "week",
        since: scorecard.since,
        until: scorecard.until,
        sort: url.searchParams.get("sort") ?? "latency_ms",
        order: url.searchParams.get("order") ?? "desc",
        total_matching: rows.length,
        limit: Number(url.searchParams.get("limit") ?? 20),
        offset: Number(url.searchParams.get("offset") ?? 0),
        returned: rows.length,
        queries: rows,
      });
    }
    if (/^\/v1\/[^/]+\/health\/failures$/.test(path)) {
      return json(route, failures);
    }

    // The create screen's own routes. All three run before any collection
    // exists, which is why none of them is scoped to one.
    if (path === "/v1/field-templates") return json(route, fieldTemplates);
    if (path === "/v1/field-types") return json(route, fieldTypes);
    if (path === "/v1/enrichment/generate-upload-url") {
      return json(route, {
        object_id: "sample-1",
        presigned_url: "https://s3.test/put/sample-1",
        download_presigned_url: "https://s3.test/get/sample-1",
      });
    }
    if (path === "/v1/enrichment/generate") {
      return json(route, generatedSchema);
    }
    if (path === "/v1/enrichment/preview") {
      return json(route, samplePreview);
    }
    if (/^\/v1\/[^/]+\/enrichment$/.test(path)) return json(route, enrichment);
    if (path === "/v1/embedding-models") {
      return json(route, {
        models: ["paraphrase-multilingual-mpnet-base-v2"],
      });
    }

    // Anything else on this service: answer empty rather than hang, and let the
    // spec's assertion on `recorder.calls` say what was unexpected.
    return json(route, {});
  });

  return recorder;
}
