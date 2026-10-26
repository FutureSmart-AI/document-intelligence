# Product capabilities

A map of what FutureSmart Document Intelligence does today, where each part runs,
how to use it, how models are configured, and what is not available yet. It
links to the detailed guides instead of repeating them. State as of 2026-10-26.

- Hosted application: https://app.futuresmart.ai/document-intelligence
- Product page: https://futuresmart.ai/solutions/document-intelligence
- Developer docs for the hosted API: https://futuresmart.ai/docs/document-intelligence
  (the guide and [API reference](https://futuresmart.ai/docs/document-intelligence/api-reference)
  are generated from the live OpenAPI)
- Self-hosting: [installation](installation.md), [configuration](configuration.md)

## 1. What the product supports today

| Capability | What it does | Where it lives / details |
| --- | --- | --- |
| Collections and upload | A collection holds documents, a field template and chat settings. Upload PDF, DOCX, TXT, Markdown, HTML, web pages (URL) and audio, one at a time or in batches. PDFs and DOCX are limited to 10 pages. | `backend/routers/knowledge_management.py`, `document_management.py`, `batch_document_management.py`; [RAG service API](api/rag-service.md) |
| Parsing modes | `document_processing_mode`: `auto` (default), `text_based`, `scanned`, `layout_aware`, `visual`, `mixed`. `auto` and `text_based` use local readers. The other four use LlamaParse and need `LLAMA_PARSE_API_KEY`; without it an explicit request returns HTTP 422 naming the key. | `workers/celery_utils/processing/processing_modes.py`; [configuration](configuration.md) |
| Structured extraction with typed field templates | Define fields with a description and a type: Text, Text (multiple values), Number (`int`/`float`), Yes/No, Date, Group of fields, Table. Templates: `resume`, `invoice`, `invoice_contract`, `contract`, `support_ticket`, `software_docs`. Fields can also be proposed from a prompt (`POST /v1/{knowledge_id}/enrichment/generate`, up to 20). | `GET /v1/field-types`, `GET /v1/field-templates`; `backend/utils/config/enrichment_config.py` |
| Metadata-aware RAG Q&A with sources | Each chat turn refines the query, lets a retrieval agent filter on extracted fields and/or search passages, verifies the retrieval, then answers. The cited documents are returned as sources. | [How chat answers questions](chat-question-answering.md) |
| Deterministic aggregation | Counts, sums, ranges and rankings over a complete filtered population are computed in code. The answer text and the chart are built from that one computation, so they show the same numbers. | [Chat question answering, step 6](chat-question-answering.md#the-path-of-one-turn) |
| Charts, including follow-up charts | An aggregate question returns a chart. A follow-up such as "chart their years" charts the previous answer's documents. | `backend/chat/chart_generator.py`, `backend/chat/local_chart_renderer.py`, `backend/chat/rag_agent/result_set.py` |
| Knowledge table and CSV export | Every document is a row and every field a typed column; search, sort, filter, open a row for the source text, and export CSV. API: `POST /v1/{knowledge_id}/documents/metadata/all` (cursor) and `/v2/...` (pages). | [Extraction context and CSV](api/extraction-context.md) |
| Suggested questions | Starter questions shown on an empty conversation. They are written by the collection owner (`PUT /v1/{knowledge_id}/chat/config`), not generated. Follow-up questions after each answer are generated. | `backend/routers/chat.py` |
| Sample collections | Two shared, read-only sample collections on the hosted app: 24 resumes and 18 invoices. See section 5. | `backend/routers/samples.py`, `backend/utils/auth/sample_access.py` |
| API keys | A signed-in owner creates a key on a collection's Share → API panel. The key works on that one collection: read, search, chat, upload and delete documents. Settings, collection deletion, sharing, keys and account data return 403. The full key is shown once. | `backend/routers/api_key_management.py`; [API keys and permissions](https://futuresmart.ai/docs/document-intelligence/authentication) |
| Usage limits | Per-owner limits on documents, URLs, collections, messages per day and month, conversations and tokens. A request over a limit returns HTTP 429 `quota_exceeded`. | `backend/routers/usage_limits.py`, `backend/utils/config/usage/owner_defaults.yaml`; [errors and limits](https://futuresmart.ai/docs/document-intelligence/errors-and-limits) |

Also in the code: batch question reports (`backend/routers/reports.py`), sharing and invitations,
an embeddable chat widget, and a ClickUp connector. This page does not cover them.

## 2. Hosted vs standalone

| | Hosted (app.futuresmart.ai) | Standalone (this repository, Docker Compose) |
| --- | --- | --- |
| Sign-in | FutureSmart SSO (email/password or Google) | One operator password (`STANDALONE_PASSWORD`), `AUTH_MODE=standalone` |
| Model credentials | FutureSmart platform credentials; an owner may add their own provider keys | The operator's keys; the default profile needs only `OPENAI_API_KEY` |
| Usage limits | Enforced. New accounts get the trial: 50 documents, 10 URLs, 10 collections, 50 messages/day, 500/month, 100 conversations/month, 1M tokens/month | Observe-only by default (`USAGE_ENFORCE_LIMITS=false`) |
| Internal accounts | A verified `futuresmart.ai` or `aidemos.com` sign-in has no trial ceilings | Not applicable |
| Charts | External FutureSmart Chart API (`CHART_API_URL` set) | In-process renderer on the same `OPENAI_API_KEY` (`CHART_API_URL` empty) |
| Sample collections | Live, with a sample picker in the hosted UI | **Hosted only.** The API and provisioning script are here, but this repository's UI has no sample picker |
| Files and queues | S3 and SQS on ECS | LocalStack by default; real S3/SQS through [the AWS guide](deployment/aws.md) |
| LlamaParse parsing modes | Available on platform credentials | Only with your own `LLAMA_PARSE_API_KEY` |
| API base URL | `https://futuresmart.ai/document-intelligence/api` (also `https://app.futuresmart.ai/api/knowledge`) | `http://localhost:8000` (direct API), `/api/knowledge` through the UI on port 8086 |

[Publication and source export](publication.md#retained-hosted-code) lists the hosted-only code kept in
this repository and how it behaves when standalone.

## 3. How to use it

**Web UI.** Create a collection: name it, pick a field template or write fields, and add documents.
Each document shows Processing, then Completed. Knowledge shows the table of extracted fields; open a row
for the text read from the file, its summary, fields and a Processing panel. Conversations is where you
ask questions; each answer has Chart, Data, Sources and Raw tabs and a Full logs view. Settings holds
fields, chat settings and suggested questions. Share → API creates API keys.

**REST API.**

- Base URL: hosted `https://futuresmart.ai/document-intelligence/api`; standalone `http://localhost:8000`.
- Auth header: `x-api-key: <key>`. Hosted callers use a collection key. Standalone server-side callers can
  use the private master key from `.env`. Never put either in browser code.
- Key endpoints:

| Purpose | Endpoint |
| --- | --- |
| Create / list collections | `POST /v1`, `GET /v1` |
| Configure fields | `PUT /v1/{knowledge_id}/config`, `POST /v1/{knowledge_id}/enrichment` |
| Upload a document / a batch | `POST /v1/{knowledge_id}/documents`, `POST /v1/{knowledge_id}/documents/batch` |
| Processing status | `GET /v1/{knowledge_id}/documents/{object_id}/status` |
| Extracted fields, one document / the table | `GET /v1/{knowledge_id}/documents/{object_id}/metadata`, `POST /v2/{knowledge_id}/documents/metadata/all` |
| Search passages | `POST /v1/{knowledge_id}/search` ([contract](api/search.md)) |
| Ask (JSON or SSE) | `POST /v1/{knowledge_id}/chat` |
| Conversations | `GET /v1/{knowledge_id}/sessions`, `GET /v1/{knowledge_id}/sessions/{session_id}` |
| Sample collections and allowance | `GET /v1/samples` |

Guides: [RAG service API](api/rag-service.md) (runnable `examples/rag_service.py`),
[search](api/search.md), [usage telemetry](api/usage-telemetry.md), hosted
[quickstart](https://futuresmart.ai/docs/document-intelligence/quickstart) and
[API reference](https://futuresmart.ai/docs/document-intelligence/api-reference). A running API serves
its full schema at `/docs` and `/openapi.json`.

**MCP.** Not available yet. Document Intelligence has no MCP server. To use a collection from an
AI agent today, call the REST API.

## 4. Model configuration

Every model call belongs to one operation. `deploy/configure.py` writes one profile from
[`deploy/model-profiles.json`](../deploy/model-profiles.json) into `.env`. Each profile lists its `settings`
(environment variables) and an `operations` map (provider, model, fallback, controlling settings).
A profile can `extends` another and override settings: `openai-groq-v1` extends `openai-v1` and changes
only extraction.

Default profile `openai-v1` (all OpenAI, one `OPENAI_API_KEY`):

| Operation | Model | Fallback | Setting |
| --- | --- | --- | --- |
| Extraction (worker) | `gpt-5.4-mini` | `gpt-5.4-nano` | `ENRICHMENT_MODEL`, `ENRICHMENT_FALLBACK_MODEL` |
| Extraction after a LlamaParse mode | `gpt-5.6-luna` | `gpt-5.4-nano` | code constant |
| Create-collection preview | `gpt-5.4-mini` | `gpt-5.4-nano` | same as extraction |
| Query refinement | `gpt-5.4-nano` | none | `RAG_CHAT_REFINED_QUERY_LLM_*` |
| Retrieval agent | `gpt-5.6-luna` | `gpt-5.4-mini` | `RAG_CHAT_AGENT_LLM_*`, `RAG_FALLBACK_*` |
| Retrieval verifier | `gpt-5.4-nano` | none | `RAG_RETRIEVAL_VERIFIER_*` |
| Answer | `gpt-5.6-luna`, or the collection's chat-config model | none | `RAG_CHAT_ANSWER_LLM_*` |
| Chart spec | `gpt-5.4-mini` | none | `CHART_EXTRACTION_MODEL` |
| Chart render (local renderer) | `gpt-5.4-mini` | deterministic bar chart | `CHART_RENDER_*` |
| Follow-up suggestions | `gpt-5.4-nano` | none | `FOLLOW_UP_LLM_*` |
| Embeddings | `text-embedding-3-small` | none | per collection |

Override order: per-collection settings (UI or API), then the global enrichment default
(`GET`/`PATCH /v1/admin/enrichment-config`, master key), then `.env`, then code defaults, which match
`openai-v1`. Saving any chat setting for a collection creates its chat-config row, and that row's model
(default `gpt-5.4-mini`) then answers. Details: [configuration](configuration.md#model-profiles).

**Fallbacks are recorded, not silent.** When a primary model fails and another serves, the chat
`usage.operations[]` row and the document `processing_telemetry.operations[]` row carry
`fallback: true`, `requested_model` and `fallback_reason`. A chart drawn by the deterministic fallback
records why in `chart_data.metadata.chart_type_source`.

**Optional providers.** LlamaParse is needed only for the four parser modes. Gemini, Groq, Anthropic and
DeepSeek can be chosen per collection, and none of them is needed for the core path. Provider overrides,
for example OpenRouter, go through the same settings; no OpenRouter key is configured anywhere.

**Current hosted extraction.** The hosted enrichment default is configured as Google
`gemini-3.1-flash-lite` with fallback `gpt-5.4-nano`. Gemini's credits are exhausted, so every hosted
extraction is served by the `gpt-5.4-nano` fallback. The model evaluation recommends `gpt-5.6-luna` at
reasoning low (`gpt-5.6-luna-low`) for extraction and `gpt-5.6-luna` for chat. See the
[model evaluation results](../evals/model_eval/RESULTS-2026-10-26.md). Hosted extraction has **not** been
switched. The switch waits for a regression run of the `profile-luna-low` config.

## 5. Sample data and suggested workflows

Two synthetic sample sets. Every person, company and address in them is invented.

- [Resumes](../backend/samples/resumes/README.md): 24 one-page PDFs, six layouts, 13 `resume` fields,
  with `ground_truth.json` and `questions.json`.
- [Invoices](../backend/samples/invoices/README.md): 18 PDFs from six vendors in USD, EUR and INR,
  10 `invoice` fields, with `ground_truth.json` and `questions.json`.

`backend/scripts/provision_sample.py` builds a sample collection from its `sample.json` through the
public API. It creates the collection with the template, uploads and waits for every document, sets
the suggested questions and swaps the live copy (`--update-questions` changes the questions alone).
A question goes into `sample.json` only after it has been answered exactly on the hosted sample.
On hosted, sample questions are charged to the sample owner, not the visitor. Each public visitor gets
20 sample questions per day and 100 in total; internal accounts are unlimited.

Suggested workflows:

1. **Try a sample.** On the hosted app, open the resumes sample and ask "Which candidates know Python and
   AWS and have at least 5 years of experience?", then "chart their years". Open Sources and Full logs.
2. **Build your own collection.** Create a collection with the `invoice` template, or write your own
   fields with types. Upload 5–10 documents and check each field in the Knowledge table against the
   document beside it.
3. **Ask, chart, export.** Ask a filter question ("which invoices are due after 1 October?"), an aggregate
   ("total billed per vendor, as a chart"), then export the table as CSV.
4. **Call the API.** Create a collection key, then upload, poll status, read
   `/v2/{knowledge_id}/documents/metadata/all`, and `POST /chat` with `"stream": false`.
   [`examples/rag_service.py`](../examples/rag_service.py) runs the same sequence against a standalone install.

## 6. Cost, latency and evaluation

**On every request.** Each chat turn returns `usage`: server latency, the model that served each operation,
input/cached/output tokens, call count, `estimated_cost_usd`, `cost_complete` and `price_table`
(currently `openai-standard-2026-10-26`). Each processed document returns `processing_telemetry`: parser and
mode, the extraction and embedding models, tokens, latency, cost and the same flags. Costs are token
counts times the list prices in `backend/chat/pricing.py`, not provider bills. An unpriced model gives
`estimated_cost_usd: null` and `cost_complete: false`, never 0. Contract: [usage telemetry](api/usage-telemetry.md).

In the UI, each answer's **Full logs** shows a "Model calls of this answer, by operation" table, and a
document's **Processing** panel shows the read, extraction and embedding steps, including any fallback.

**Accuracy is not shown on ordinary requests.** Nothing in `usage` or `processing_telemetry` is an accuracy
score. Accuracy comes only from the evaluation harness.

**Model evaluation harness** ([README](../evals/model_eval/README.md)). It runs the product's own API in a
clean single-key standalone install. The extraction suite grades every field of the 24 resumes and
18 invoices, and the RAG suite asks 78 cases on each config's own collections. Every config, dataset and
git SHA is hashed. The dataset is `document-intelligence-model-eval` v1. Each run is recorded as a
LangSmith experiment. It runs **on demand only**: GitHub Actions → Model evaluation → Run workflow, or
`evals/model_eval/run.py` locally. The hosted question harness is in
[`evals/metadata_qa/`](../evals/metadata_qa/README.md).

Headline results ([RESULTS-2026-10-26](../evals/model_eval/RESULTS-2026-10-26.md), two runs, strict
two-judge grading):

- Extraction: `gpt-5.6-luna` at reasoning low got 980/984 fields (99.6%) and 360/360 invoice fields,
  at $0.00052 per document. The current `gpt-5.4-nano` at medium got 967/984 (98.3%) at $0.00104.
- RAG: the current chat models (`gpt-5.6-luna` agent and answer) got 64–69/78. That beats `gpt-5.4-mini`
  (60/78) and `gpt-5.4-nano` (57–59/78), and luna is also the cheapest per answer ($0.006).
- Every config gets 100% on aggregation, charts, comparison, counts, exact and negation filters, and
  ranking.

## 7. Limitations and not yet available

- No MCP server ([developer docs overview](https://futuresmart.ai/docs/document-intelligence)).
- Hybrid (0–3/4) and multi-document synthesis (0–2/4) questions are weak in every evaluated config. The
  answers leave out qualifying documents ([RESULTS](../evals/model_eval/RESULTS-2026-10-26.md#rag-78-cases-per-config)).
- Follow-up chart filter loss ("chart their years" charting 14 people or 7 of 8) reproduced intermittently.
  It is fixed in source (`backend/chat/rag_agent/result_set.py`, `tests/unit/test_follow_up_result_set.py`)
  but not yet deployed to hosted on 2026-10-26, and no evaluation run has measured the fix yet.
- Invoice `payment_terms` capitalisation drifts between runs ("Net 30" / "net 30"). The meaning is the same
  ([EXTRACTION-YEARS-2026-10-25](../backend/samples/resumes/EXTRACTION-YEARS-2026-10-25.md)).
- Hosted: Gemini credits are exhausted, so every extraction pays for a failing Gemini attempt before the
  nano fallback serves ([RESULTS](../evals/model_eval/RESULTS-2026-10-26.md#recommendation-per-operation)).
- No OpenRouter key is configured, so OpenRouter overrides are untested ([eval README](../evals/model_eval/README.md#configurations)).
- API keys are collection-scoped, with a fixed set of key actions. A key also carries its creator's role.
  The earlier finding that keys carried the owner's full permissions is fixed (`tests/unit/test_api_key_scope.py`).
- Upload size differs by path. The direct API upload allows 5 MB (`backend/utils/validation/file_validation_utils.py`),
  while the web UI allows 50 MB through a presigned upload. Both paths limit PDFs and DOCX to 10 pages.
- In the Create wizard, a file that fails the 10-page check is dropped without a message (seen on hosted, 2026-10-23). Knowledge → Files reports it.
- The invoice question "which invoices include cloud infrastructure charges?" returned 3 of 4 on hosted, so it
  was removed from the sample's suggested questions ([invoice verification](../backend/samples/invoices/VERIFICATION.md)).
- The usage limit message says "upgrade your plan", but the app has no upgrade path.
- Sample collections exist only on hosted. A standalone install has no sample picker.
- Standalone with LocalStack loses original uploads and queued work when LocalStack restarts ([installation](installation.md)).
