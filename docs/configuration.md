# Runtime configuration

`deploy/configure.py` generates an ignored root `.env`. `.env.example` lists
the environment contract; it contains placeholders and emulator credentials.
Real secrets are injected at runtime and excluded from container contexts.

| Setting | Use |
| --- | --- |
| `OPENAI_API_KEY` | Required, and the only provider key the default profile needs: embeddings, extraction, collection preview, retrieval, answers, follow-ups and charts |
| `GROQ_API_KEY` | Optional. `openai-groq-v1` profile enrichment, or a Groq model chosen per collection |
| `ANTHROPIC_API_KEY` | Optional. Anthropic models chosen per collection or for the optional extraction-validation judge |
| `LLAMA_PARSE_API_KEY` | Optional. Scanned/layout-aware/visual/mixed parsing modes. Without it an upload that asks for one of those modes is refused with HTTP 422 naming the key; an `auto` upload whose content looks scanned or table-heavy is read with the local text extractor and the document's processing telemetry records why |
| `CHART_API_URL`, `CHART_SERVICE_KEY` | Optional hosted FutureSmart Chart API. Unset (the default), charts are drawn in-process: one OpenAI call (`CHART_RENDER_MODEL`) picks the chart type and columns, and code maps the computed rows into the same `chart_output` contract |
| `FIRECRAWL_API_KEY` | Optional configured web scraping |
| `FASTAPI_SECRET_KEY` | Private API master/session signing key; never a public browser key |
| `STANDALONE_PASSWORD`, `STANDALONE_OWNER_EMAIL` | Independent UI sign-in and operator identity |
| `USAGE_CREDENTIALS_MASTER_KEY`, `USAGE_CREDENTIALS_HASH_SALT` | Retained provider-credential encryption and hashing |
| `CONNECTOR_SECRET_KEY` | Optional retained connector-credential encryption |
| `API_KEY_HASH_SECRET` | Retained generated-key pepper when rotating an existing master; default is the master |

## Model profiles

`deploy/configure.py` writes one versioned profile from `deploy/model-profiles.json`
into `.env` and records it as `MODEL_PROFILE`. Each profile's `settings` are the
environment variables written; its `operations` map documents, per operation, the
provider, exact model, fallback and the settings that control it. A profile may
`extends` another and override individual settings.

| Profile | Keys | Extraction (fallback) | Everything else |
| --- | --- | --- | --- |
| `openai-v1` (default) | `OPENAI_API_KEY` | `gpt-5.4-mini` (`gpt-5.4-nano`) | as below |
| `openai-groq-v1` | `OPENAI_API_KEY`, `GROQ_API_KEY` | Groq `openai/gpt-oss-120b` (`gpt-5.4-nano`) | as `openai-v1` |

What `openai-v1` runs, per operation (all OpenAI):

| Operation | Model | Fallback | Settings |
| --- | --- | --- | --- |
| Extraction (worker) | `gpt-5.4-mini` | `gpt-5.4-nano` | `ENRICHMENT_MODEL`, `ENRICHMENT_FALLBACK_MODEL` |
| Extraction after a parser mode | `gpt-5.6-luna` | `gpt-5.4-nano` | code constant; needs `LLAMA_PARSE_API_KEY` |
| Create-collection preview | `gpt-5.4-mini` | `gpt-5.4-nano` | same chain as extraction |
| Embeddings | `text-embedding-3-small` | none | per collection |
| Query refinement | `gpt-5.4-nano` | none | `RAG_CHAT_REFINED_QUERY_LLM_PROVIDER`/`_MODEL` |
| Retrieval agent | `gpt-5.6-luna` | `gpt-5.4-mini` | `RAG_CHAT_AGENT_LLM_*`, `RAG_FALLBACK_PROVIDER`/`_MODEL` |
| Retrieval verifier | `gpt-5.4-nano` | none | `RAG_RETRIEVAL_VERIFIER_PROVIDER`/`_MODEL` |
| Answer | `gpt-5.6-luna`, or the collection's chat-config model (see below) | none | `RAG_CHAT_ANSWER_LLM_PROVIDER`/`_MODEL` |
| Follow-up questions | `gpt-5.4-nano` | none | `FOLLOW_UP_LLM_PROVIDER`/`_MODEL` |
| Chart spec (which field, which reduction) | `gpt-5.4-mini` | none | `CHART_EXTRACTION_MODEL` |
| Chart render (type and columns; local renderer only) | `gpt-5.4-mini` | deterministic bar chart, reason recorded | `CHART_RENDER_PROVIDER`/`_MODEL` |
| Audio transcription | `whisper-1` | none | `TRANSCRIPT_PROVIDER` |
| Extraction-validation judge | off | | `EXTRACTION_VALIDATION_LLM_ENABLED` |

**Which model answers.** A collection with no chat-config row answers with
`RAG_CHAT_ANSWER_LLM_MODEL` (`gpt-5.6-luna`). Saving any chat setting for a
collection (UI Settings, or `PUT /v1/{knowledge_id}/chat/config`) creates its row,
and the row's `llm_model` (default `gpt-5.4-mini` unless the request names one)
then answers that collection. Send `llm_provider`/`llm_model` in that request to
choose explicitly.

Scanned, layout and visual modes enrich with `gpt-5.6-luna`. New collections
embed with OpenAI `text-embedding-3-small`
(`backend/utils/config/knowledge_defaults.py`); a collection keeps the embedding
model it was created with. The optional extraction-validation LLM judge is off in
both profiles. Left unset, it turns on only when its provider's key exists
(Anthropic by default) and logs which state it chose. Set
`EXTRACTION_VALIDATION_LLM_ENABLED=true` with an Anthropic key, or point
`EXTRACTION_VALIDATION_PROVIDER`/`_MODEL` at another configured model, to enable it.

No Gemini, Anthropic, Groq, LlamaParse, OpenRouter or Chart API credential is
needed for the core path. Code defaults match `openai-v1`, so a `.env` missing
these settings still runs on OpenAI only.

Advanced overrides still apply in the existing order: per-collection settings in
the UI/API, then the global admin default, then the environment variables in
`.env`, then code defaults. Edit `.env` directly to change a single node, such as
`RAG_CHAT_ANSWER_LLM_PROVIDER`/`RAG_CHAT_ANSWER_LLM_MODEL`. Profiles change
defaults only. They do not rewrite existing collections or reindex vectors.

## Cost estimates and the price table

Chat `usage` and document `processing_telemetry` report `estimated_cost_usd`
([contract](api/usage-telemetry.md)). The figure is the token count multiplied
by the list prices in `backend/chat/pricing.py`, the only price table, which the
API also uses to price worker measurements. `PRICE_TABLE_VERSION`
(currently `openai-standard-2026-10-26`) travels with every estimate.

When a rate changes, edit `PRICE_PER_1K` and bump the version in the same
commit. Verified OpenAI standard-tier rates on 2026-10-26, per 1M tokens
(input / cached input / cache write / output):

- `gpt-5.6-luna`: 0.20 / 0.02 / 0.25 / 1.20
- `gpt-5.6-terra`: 2.00 / 0.20 / 2.50 / 12.00
- `gpt-5.4-nano`: 0.20 / 0.02 / none / 1.25
- `gpt-5.4-mini`: 0.75 / 0.075 / none / 4.50
- `text-embedding-3-small`: 0.02

A model missing from the table, such as a Gemini enrichment model, reports
`estimated_cost_usd: null` and `cost_complete: false`. It is never reported
as free.

Compose supplies the internal database, Qdrant, Redis and emulator endpoints.
The private Knowledge callback key matches the API master. Workload identity
uses `CELERY_BROKER_URL=sqs://` with AWS task roles in hosted deployments;
local dummy AWS keys belong only to the emulator. Never reuse those keys in AWS.

Keep private auth/callback credentials distinct from any public UI gateway key.
Keep generated-key HMAC material and encryption salts stable across upgrades.
Do not regenerate `.env` or blindly rotate these values on a retained database.
Hosted SSO is an optional deployment profile; the default Compose installation
uses local operator login and does not call a private identity service.

Frontend provider credentials are never configured. The standalone Dockerfile
builds `NEXT_PUBLIC_AUTH_MODE=standalone`. The hosted profile may configure public
auth/host/voice settings; its public gateway value must never be the private
Knowledge master/session/callback key. Empty voice configuration hides optional
voice controls, as in the existing independent profile.
