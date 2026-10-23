/**
 * Every HTTP endpoint this app calls.
 *
 * All of them belong to the knowledge service — the contract published at
 * `https://app.futuresmart.ai/api/knowledge/docs` (mirrored in
 * `knowledge-openapi.json` beside this file, which `__tests__/apiContract.test.ts`
 * checks every entry below against).
 *
 * A collection **is** a knowledge base here. There is no agent behind it: the
 * documents, the extracted fields, the answer settings, the conversations and
 * the feedback all live on this one service, so `?collection=` carries a
 * `knowledge_id` and nothing in this product asks the chat, dashboard or usage
 * services for anything.
 *
 * The only requests this app makes that are not in the list below are the auth
 * service's own (`GET /user/me`, `POST /refresh`, the SSO redirects) — the
 * knowledge service publishes no session or identity routes, so those cannot
 * move here.
 */
export const APIS = {
  /**
   * The collections themselves. `GET` answers `{ owner_id, knowledge_services:
   * string[], count }` — ids only, no names — so the board reads each one's
   * config for its name (see `data/collections.ts`).
   */
  KNOWLEDGE: "/api/knowledge/v1",
  KNOWLEDGE_ITEM: "/api/knowledge/v1/{knowledge_id}",
  /**
   * `GET …/validate?owner_id=…&knowledge_id=…` — `{ valid: boolean }`, the
   * service's own words: "validate if a knowledge exists and belongs to the
   * specified owner".
   *
   * It is the only route that answers who owns a collection: the list route
   * returns bare ids, and the config carries no `owner_id`. That makes it what
   * the board's "Shared with me" tab is built on — a collection the account can
   * read but does not own is one somebody invited it to.
   */
  KNOWLEDGE_VALIDATE: "/api/knowledge/v1/validate",
  /** Name, description, embedding model and the two metadata levels. */
  KNOWLEDGE_CONFIG: "/api/knowledge/v1/{knowledge_id}/config",

  /**
   * Extracted fields. `GET` returns `{ fields: [...] }`; `POST` creates them,
   * and is what the create screen writes its confirmed list with — one call
   * for the whole list, every entry carrying `is_custom: true`.
   */
  ENRICHMENT: "/api/knowledge/v1/{knowledge_id}/enrichment",
  /**
   * The catalogue of starting points — "Resumes / CVs", "Invoices" and the
   * rest — each with the fields that kind of document usually carries, already
   * named and typed.
   *
   * Read, never hardcoded. The five choices this screen used to ship as a
   * constant were the same five this route returns, right up until the service
   * added a sixth, and a picker that cannot grow is a picker that is wrong the
   * day the backend improves. `key: "default"` is the "Something else" entry
   * and comes back with no fields.
   */
  FIELD_TEMPLATES: "/api/knowledge/v1/field-templates",
  /**
   * Every type a field can be read as, with the words to show it in and
   * whether picking it opens a sub-field builder.
   *
   * `requires_schema` is the flag to branch on — it is `true` for exactly
   * `dict` and `List[dict]` today, and a hardcoded pair of type strings is how
   * this app went stale the last time the catalogue grew.
   */
  FIELD_TYPES: "/api/knowledge/v1/field-types",

  /**
   * Where a sample document goes so the schema call can read it.
   *
   * `POST` it a `file_name` and a `content_type`, `PUT` the bytes at the
   * `presigned_url` it answers with, then hand the `download_presigned_url`
   * back as `file_url`. The schema call takes a URL, not an upload, and the
   * document never enters a collection.
   */
  SCHEMA_UPLOAD_URL: "/api/knowledge/v1/enrichment/generate-upload-url",
  /**
   * "What should we pull out of each document?", answered before any
   * collection exists.
   *
   * Form-encoded, taking `collection_name`, `service_description`,
   * `extraction_instructions` and at most one `file_url`. It writes nothing —
   * `run.persisted` is `false` — and answers with the fields it proposes, the
   * optional defaults it would keep or drop, and anything it rejected. That
   * answer is what the confirm step edits; the fields are created for real,
   * once, by `POST …/{knowledge_id}/enrichment` at the end.
   *
   * Session-only (a master key is refused with `401`) and metered against the
   * owner's monthly LLM budget (`429` before it spends anything).
   */
  SCHEMA_GENERATE: "/api/knowledge/v1/enrichment/generate",
  /**
   * One document, run against the fields as they currently stand, before a
   * collection exists to hold either.
   *
   * Multipart: `fields` as a JSON string, plus exactly one of `file`,
   * `file_url` or `sample_key`. Answers the values it pulled out, a per-field
   * `extracted`/`not_found` verdict, and any field it found on the document
   * that nobody asked for. Nothing is saved.
   *
   * Session-only, like the schema call. `422` means the document carried no
   * readable text — a scan, usually — and `502` is a real extraction failure,
   * which is not the same thing as a field simply not being found.
   */
  SCHEMA_PREVIEW: "/api/knowledge/v1/enrichment/preview",

  /** Document list — v2 carries the presigned original/parsed URLs. */
  DOCUMENTS: "/api/knowledge/v2/{knowledge_id}/documents",
  /** Adding one document: multipart `text` or `weblink` (files go by batch). */
  ADD_DOCUMENT: "/api/knowledge/v1/{knowledge_id}/documents",
  /**
   * Registering uploaded files, one or many.
   *
   * Files reach S3 through `POST …/upload-urls/batch` (the shared upload
   * service's own call) and are registered here — the batch pair is the only
   * upload path this product uses, so a single file answers with a `batch_id`
   * like any other batch and the list has one shape to track.
   */
  ADD_DOCUMENTS_BATCH: "/api/knowledge/v1/{knowledge_id}/documents/batch",

  /**
   * Answering: settings, the models on offer, and the question itself.
   *
   * `POST …/chat` answers `text/event-stream` while `stream` is true, and its
   * events carry the same fields the shared chat stack already reads (`chunk`,
   * then a final event with `session_id`, `chat_id`, `follow_up` and
   * `data.knowledge`) — see `data/knowledgeChat.ts`.
   */
  CHAT: "/api/knowledge/v1/{knowledge_id}/chat",
  CHAT_CONFIG: "/api/knowledge/v1/{knowledge_id}/chat/config",
  CHAT_MODELS: "/api/knowledge/v1/chat-models",

  /** Conversations this collection has had, and one thread of them. */
  SESSIONS: "/api/knowledge/v1/{knowledge_id}/sessions",
  SESSION: "/api/knowledge/v1/{knowledge_id}/sessions/{session_id}",
  /** Thumbs on one answer: `{ feedback: "positive" | "negative" | null }`. */
  MESSAGE_FEEDBACK:
    "/api/knowledge/v1/{knowledge_id}/sessions/{session_id}/messages/{chat_id}/feedback",

  /**
   * Usage, and the keys it is spent through — both scoped to the **account**,
   * not to a collection.
   *
   * The service moved this whole surface under `/v1/owners/{owner_id}/…`; the
   * knowledge-scoped paths this app used to name (`/v1/{knowledge_id}/usage/…`)
   * are gone and answer 404. Everything here is therefore read once for the
   * signed-in owner rather than once per collection and summed:
   *
   * - `USAGE_LIMITS` — month-to-date counters against the account's platform
   *   limits, in one request. `null` in a limit means unlimited.
   * - `USAGE_LIMITS_SCREEN` — the same allowances, already resolved into the
   *   rows the Limits tab draws: used, limit, percent, whether the ceiling is
   *   lifted and whether it is enforced. Preferred over `USAGE_LIMITS` for
   *   that tab, because every judgement it encodes is the service's own —
   *   re-deriving a percentage here is a second number that can disagree.
   * - `USAGE_ANALYTICS` — the flexible one the Usage screen is built on:
   *   filters, `group_by` (`["knowledge_id"]` gives the per-collection
   *   breakdown) and `time_bucket`, answering with cost broken out.
   * - `USAGE_SERVICE_OPTIONS` — the catalogue behind the filter dropdowns.
   */
  USAGE_LIMITS: "/api/knowledge/v1/owners/{owner_id}/usage/limits/status",
  USAGE_LIMITS_SCREEN:
    "/api/knowledge/v1/owners/{owner_id}/usage/limits/screen",
  USAGE_ANALYTICS: "/api/knowledge/v1/owners/{owner_id}/usage/analytics/query",
  USAGE_SERVICE_OPTIONS:
    "/api/knowledge/v1/owners/{owner_id}/usage/analytics/service-options",

  /**
   * Bring-your-own-key credentials, stored per **account** — the service's own
   * words: a stored key "applies to every knowledge base the owner has". Listed
   * with the keys masked; the value sent is encrypted and never comes back.
   */
  CREDENTIALS: "/api/knowledge/v1/owners/{owner_id}/usage/credentials",
  CREDENTIAL:
    "/api/knowledge/v1/owners/{owner_id}/usage/credentials/{provider}",
  CREDENTIAL_PROVIDERS:
    "/api/knowledge/v1/owners/{owner_id}/usage/credentials/providers",
  /**
   * Batch query reports — a set of questions run together against one
   * collection, on demand or on a schedule.
   *
   * Collection-scoped by path (`{knowledge_id}`), the way every other
   * collection route here is: there is no `agent_id` query parameter on this
   * service. The wire shapes are the ones `@/platform/types/batch-reports`
   * describes for the dashboard's own reports — same fields, same run
   * lifecycle — so this app reuses those types rather than restating them.
   */
  REPORTS: "/api/knowledge/v1/{knowledge_id}/reports",
  /** The per-collection cap on how many questions one report may hold. */
  REPORT_SETTINGS: "/api/knowledge/v1/{knowledge_id}/reports/settings",
  REPORT: "/api/knowledge/v1/{knowledge_id}/reports/{report_id}",
  REPORT_RUNS: "/api/knowledge/v1/{knowledge_id}/reports/{report_id}/runs",
  REPORT_RUN:
    "/api/knowledge/v1/{knowledge_id}/reports/{report_id}/runs/{run_id}",
  /** The cheap poll — status and progress counters, no answers. */
  REPORT_RUN_STATUS:
    "/api/knowledge/v1/{knowledge_id}/reports/{report_id}/runs/{run_id}/status",
  /** `?format=json|md|csv`. */
  REPORT_RUN_EXPORT:
    "/api/knowledge/v1/{knowledge_id}/reports/{report_id}/runs/{run_id}/export",
  REPORT_RUN_CANCEL:
    "/api/knowledge/v1/{knowledge_id}/reports/{report_id}/runs/{run_id}/cancel",

  /**
   * Sharing a collection with people, by email.
   *
   * `GET` answers `{ invited_users: [{ email, role, invited_at }] }`; the owner
   * is not in that list, so the People card puts them at the top itself.
   */
  INVITATIONS: "/api/knowledge/v1/{knowledge_id}/invitations",
  INVITATION: "/api/knowledge/v1/{knowledge_id}/invitations/{user_email}",

  /**
   * How the shared chat looks — heading, greeting, brand colour, launcher icon
   * and chat avatar.
   *
   * `POST` takes **form fields, not JSON** (an uploaded icon is a file), so
   * `data/widgetSettings.ts` names the multipart content type explicitly; this
   * axios instance defaults every body to JSON, and a JSON body here answers
   * 200 and stores nothing.
   */
  WIDGET_CONFIG: "/api/knowledge/v1/{knowledge_id}/widget-config",
  WIDGET_ICONS: "/api/knowledge/v1/widget-config/default-icons",
  WIDGET_AVATARS: "/api/knowledge/v1/widget-config/default-avatars",

  /**
   * API keys, so someone's own code can ask this collection questions.
   *
   * Account-scoped routes with an optional `knowledge_id` in the *body*: a key
   * created with one is scoped to that collection, and the list carries the
   * `knowledge_id` each key was minted for. Only the mask (`key_prefix`) comes
   * back after creation — the full key is shown once, at creation, and never
   * again.
   */
  API_KEYS: "/api/knowledge/v1/api-keys",
  API_KEY_REVOKE: "/api/knowledge/v1/api-keys/revoke",

  /**
   * Analytics for one collection — the health namespace.
   *
   * The service publishes these in the shapes the agent dashboard's analytics
   * page already consumes (its own docs say the node aggregates match "the
   * NL2SQL analytics contract this repo's scorecard tracks parity against"), so
   * the Analytics screen renders that page's own toolbar, tabs, charts and
   * tables over these four routes rather than a second implementation of them.
   * `data/analytics.ts` reconciles the handful of fields that genuinely differ.
   */
  HEALTH_SCORECARD: "/api/knowledge/v1/{knowledge_id}/health/scorecard",
  HEALTH_TRENDS: "/api/knowledge/v1/{knowledge_id}/health/trends",
  HEALTH_QUERIES: "/api/knowledge/v1/{knowledge_id}/health/queries",
  /**
   * The Cost tab's "Refresh costs": reconciles unpriced queries for the same
   * window the toolbar shows (`period`, or `start_date`/`end_date`).
   */
  HEALTH_SYNC_COSTS: "/api/knowledge/v1/{knowledge_id}/health/sync-costs",
  /**
   * Why questions went unanswered. Not read by a screen today: the product's
   * Analytics has five tabs plus Model performance, and the unanswered counts
   * live on Quality. Kept named here because the route exists and the omission
   * is deliberate.
   */
  HEALTH_FAILURES: "/api/knowledge/v1/{knowledge_id}/health/failures",
  /**
   * Every enrichment attempt, with `include_field_data` naming the fields that
   * attempt was asked for. That list is the schema a document was actually
   * processed under — the Knowledge table reads it so a field added later is
   * shown as never extracted rather than as "Not found".
   */
  HEALTH_ENRICHMENT_DOCUMENTS:
    "/api/knowledge/v1/{knowledge_id}/health/enrichment/documents",
} as const;
