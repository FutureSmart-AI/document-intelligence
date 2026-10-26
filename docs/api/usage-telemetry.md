# Usage and processing telemetry

Two read-only contracts report what a request took and roughly what it cost:

- `usage` on every chat turn (`POST /v1/{knowledge_id}/chat`, the final SSE
  event, and each assistant message of `GET /v1/{knowledge_id}/sessions/{session_id}`)
- `processing_telemetry` on every processed document
  (`GET /v1/{knowledge_id}/documents/{object_id}`, and each row of
  `GET /v1/{knowledge_id}/documents` and `GET /v2/{knowledge_id}/documents`)

Both are measurements of the request itself. They carry no accuracy score.

## Cost estimates

Costs are **estimates**: token counts multiplied by the list prices in
`backend/chat/pricing.py`, a dated table named by `price_table`
(e.g. `openai-standard-2026-10-26`). They are not provider bills. Cached input
tokens are priced at the cached rate. Cache-write tokens are priced at the
cache-write rate where a model has one.

A model missing from the table is **unpriced**. Its `estimated_cost_usd` is
`null`, never `0`. The model is listed in `unpriced_models` and `cost_complete`
is `false`. The total then covers only the priced operations, so it is a lower
bound. When nothing could be priced, the total is `null`.

## Chat turn: `usage`

```json
"usage": {
  "latency_ms": 8120,
  "input_tokens": 21000,
  "cached_input_tokens": 4000,
  "output_tokens": 700,
  "total_tokens": 21700,
  "calls": 6,
  "estimated_cost_usd": 0.0062,
  "cost_complete": true,
  "unpriced_models": [],
  "price_table": "openai-standard-2026-10-26",
  "operations": [
    {"operation": "retrieval_agent", "provider": "openai", "model": "gpt-5.4-mini",
     "requested_model": "gpt-5.6-luna", "fallback": true, "fallback_reason": "RateLimitError: …",
     "calls": 3, "input_tokens": 15000, "cached_input_tokens": 4000, "output_tokens": 400,
     "latency_ms": 3900, "estimated_cost_usd": 0.0045}
  ],
  "operation_latency_ms": {"retrieval_agent": 4200, "answer": 2500},
  "service_calls": []
}
```

| Field | Meaning |
| --- | --- |
| `latency_ms` | Server wall time of the turn's graph run (refinement to follow-ups). |
| `input_tokens`, `cached_input_tokens`, `output_tokens`, `total_tokens`, `calls` | Sums over chat-model calls. Embedding tokens are excluded and appear on their own row. |
| `operations[]` | One row per `(operation, provider, model, requested_model)`. |
| `operations[].operation` | `query_refinement`, `retrieval_agent`, `retrieval_verifier`, `answer`, `chart_spec`, `chart_render`, `follow_up`, `embeddings`, or `other_calls`. |
| `operations[].provider` / `model` | The model that **served** the calls. |
| `operations[].fallback` / `requested_model` / `fallback_reason` | `true` when the primary model failed and another model served the call. `requested_model` names the model that failed. |
| `operations[].failed_calls` | Calls that raised. These are timed but not priced. |
| `operations[].latency_ms` | Time spent inside this row's model calls. |
| `operation_latency_ms` | Wall time of each operation's stage, including tool and retrieval time. |
| `service_calls` | Unchanged per-service records kept for existing consumers. |

Every model call made during the turn is counted by a run-level LangChain
callback (`backend/chat/run_usage.py`). Calls that no operation claims are
listed as `other_calls`, so no call is dropped from the totals. Query embeddings
are reported by the embedding call sites. The Analytics health event stores the
same token and cost totals.

The block is stored on `knowledge_chat_logs.usage` (migration 055) and returned
unchanged when the thread is read back. Turns stored before that migration have
`usage: null`.

## Document: `processing_telemetry`

```json
"processing_telemetry": {
  "total_ms": 9100,
  "parse": {"parser": "local_text", "mode": "text_based", "ms": 420},
  "operations": [
    {"operation": "extraction", "provider": "openai", "model": "gpt-5.4-nano",
     "requested_provider": "google", "requested_model": "gemini-3.1-flash-lite",
     "fallback": true, "fallback_reason": "…", "calls": 1, "input_tokens": 12000,
     "cached_input_tokens": 2000, "output_tokens": 900, "latency_ms": 7300,
     "estimated_cost_usd": 0.003565},
    {"operation": "embeddings", "provider": "openai", "model": "text-embedding-3-small",
     "calls": 1, "input_tokens": 5000, "output_tokens": 0, "latency_ms": 600,
     "estimated_cost_usd": 0.0001}
  ],
  "estimated_cost_usd": 0.003665,
  "cost_complete": true,
  "unpriced_models": [],
  "price_table": "openai-standard-2026-10-26",
  "status": "completed",
  "recorded_at": "2026-10-26T10:00:00+00:00"
}
```

| Field | Meaning |
| --- | --- |
| `total_ms` | Whole processing task, as also stored in `task_duration_ms`. |
| `parse` | The reader: `parser` (`local_text`, `llamaparse`, `transcription`, `plain_text`, or a web scraper), `mode` (the mode that ran), `ms`. It may also carry `requested_mode`, `pages`, `llama_parse_credits`, `mode_auto_shifted_from` and `fallback_reason`. |
| `operations[]` (`extraction`) | Enrichment attempts, grouped by the model that served them. `calls` counts attempts, retries included. `failed_calls` and `last_failure` appear when attempts failed. |
| `operations[]` (`embeddings`) | Embedding requests for the document's vectors. |

The worker records raw measurements without prices on
`document.processing_telemetry` (migration 056). It writes them when the
document completes or fails. The API prices them on read with the same table
the chat `usage` uses. A crawled web source rolls its child pages into the
parent document's record. `processing_telemetry` is `null` in three cases: the
document is still processing, it was processed before migration 056, or its
worker ran without the column.
