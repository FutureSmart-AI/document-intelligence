# Product architecture

The complete product and the service API share one backend, one ingestion path
and one retrieval/chat engine. The frontend is optional. The ingestion worker
is required for uploads to become searchable.

```text
Browser -> static UI/proxy -> FastAPI -> PostgreSQL (configuration/status)
Service client ------------> FastAPI -> Qdrant (retrieval) -> grounded chat
                               |
                         S3 file + SQS task
                               |
                         Celery ingestion worker
                               |
                     extraction/indexing -> PostgreSQL + Qdrant
```

The worker calls the owning API using the configured private callback credential.
Redis supplies the Celery result backend. LocalStack emulates S3/SQS in Compose;
hosted deployments use durable AWS services. API, worker and UI can scale and
deploy separately while remaining source-owned by this repository.

`backend/chat/` owns direct chat, including retrieval, generation and citations.
The optional external Chat orchestrator is not required. Standalone auth is
local to the API; optional hosted authentication is a separate configuration.
Usage capture, report scheduling, connector polling and external traces are
disabled in the independent Compose profile. Existing optional capabilities
remain in source; this packaging does not change their business logic.

API containers copy `backend/` into `/app`, preserving the existing `chat`,
`routers` and `utils` imports and resource locations. Worker containers copy
`workers/` into their own `/app`; their modules are a separate process context.
Do not combine those import roots into one Python environment.

Public search document IDs are hard scope. Internal chat relevance hints retain
their existing softer semantics; see [search](api/search.md). Neither packaging
nor the example client introduces another RAG implementation.
