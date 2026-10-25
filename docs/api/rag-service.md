# RAG service API

> Using the hosted product? The [developer docs](https://futuresmart.ai/docs/document-intelligence)
> have a quickstart, API key guidance, streaming, errors and the full
> [API reference](https://futuresmart.ai/docs/document-intelligence/api-reference).
> This page covers a local or self-hosted installation.

Use the same stack and ingestion workers as the full product. Start without
the `ui` profile, or stop `ui` before service-only verification. All routes below
are relative to the **direct API**, `http://localhost:8000` in Compose. A deployed
gateway may add `/api/knowledge`; include it once in your base URL. The UI proxy
uses that prefix, while direct `/v1` calls need none.

The supported server-side example uses the private API master in an `x-api-key`
header. Supply it as `DI_API_KEY` in the caller's environment or use the private
generated `.env` with `--env-file`. Do not put it in command arguments, committed
examples, browser JavaScript or browser storage. Local UI login uses an HttpOnly
session cookie. Scoped generated API keys follow their existing permissions;
the master is an operator credential, not an end-user browser credential.

```sh
docker compose up -d --build
docker compose --profile ui stop ui
python examples/rag_service.py --env-file .env
```

The standard-library client makes the requests below, checks asynchronous
completion, verifies the exact original fixture text in retrieved chunks and
checks a grounded answer for both known fixture facts. Its output contains
source IDs and response bodies, never credentials. Save that output privately
when using your own documents.

## Existing request sequence

1. `POST /v1`, JSON
   `{"owner_id":"owner@example.test","collection_name":"API service example"}`.
   The response contains `status`, `knowledge_id` and `message`.
2. `PUT /v1/{knowledge_id}/config`, JSON `{}` selects existing defaults. The
   same endpoint accepts the configuration documented by the running `/docs`.
3. `POST /v1/{knowledge_id}/documents`, multipart fields `file` and
   `document_processing_mode=text_based`. Upload the original
   `examples/northstar-policy.txt`; record returned `object_id`.
4. Poll `GET /v1/{knowledge_id}/documents/{object_id}/status`. Its response is
   `{"knowledge_id":"...","object_id":"...","status":"completed","error_detail":null}`
   when ready. Upload acceptance alone does not mean indexed. Wait through
   queued/processing states; stop on `failed` and inspect the retained reason.
5. `POST /v1/{knowledge_id}/search`, JSON:

```json
{
  "query": "What is Northstar Research's daily meal limit and receipt deadline?",
  "object_ids": ["<returned-object-id>"],
  "num_chunks": 3,
  "mode": "hybrid"
}
```

The response is `{"chunks":[...]}`. Each chunk carries `id`, `knowledge_id`,
`object_id`, `chunk_text`, score and retrieval metadata. Match `object_id` to
the upload and compare `chunk_text` with the original source. Your application
can use these chunks as context in its own workflow.

6. Or call product-owned chat with `POST /v1/{knowledge_id}/chat`, JSON:

```json
{
  "query": "What is Northstar Research's daily meal limit and receipt deadline?",
  "stream": false
}
```

The JSON response includes `response`, `query`, `session_id`, optional `chat_id`,
`data`, answer status, follow-ups and usage. Retrieval evidence is nested under
`data.knowledge`; inspect returned source identifiers rather than treating answer
text alone as evidence. Omit `session_id` to start a session or send the returned
ID on later turns. With `stream=true` (the default), the endpoint returns SSE.

The example fixture's facts are INR 2,450 per day and receipts within 14 calendar
days. These are synthetic installation controls, not a general accuracy claim.

## Errors and scope

Missing/invalid private credentials are refused; no frontend is needed to check
authentication. Existing permission checks may return 403, missing resources
404, invalid requests 400/422 and configured usage limits 429. Processing errors
are also surfaced through document status after an accepted upload. Provider
access/configuration failures must be diagnosed privately in API/worker logs.

Search `object_ids` is a hard allowlist in both dense and hybrid modes. Omitted
or null permits the collection; `[]` returns no chunks; unknown/deleted IDs never
broaden. Unsupported fields such as `filters` return 422. See the exact
[search contract](search.md). Chat retains its existing internal relevance
behavior and has no new hard document-scope request field.

The running `/docs` and `/openapi.json` describe the complete existing contract,
including extraction configuration and generated-key permissions. This guide
covers the small service installation path and does not introduce new routes.
