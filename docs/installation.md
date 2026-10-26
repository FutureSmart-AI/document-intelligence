# Installation and persistence

Use a single clone and Docker Compose v2. The images supply Python 3.11 and
Bun 1.4.0; no host Python packages or sibling repositories are required.
The first build downloads substantial Python/model dependencies.

1. Set `OPENAI_API_KEY` privately in your shell. It is the only key the default
   `openai-v1` model profile needs: upload, extraction, metadata-aware Q&A,
   charts and the REST API all run on it. Optionally set `LLAMA_PARSE_API_KEY`
   for scanned, layout-aware and visual parsing modes before running step 2.
2. Run `python deploy/configure.py` from the repository root. It writes an
   ignored private `.env`, generates installation secrets, records the model
   profile, and refuses to overwrite existing configuration. Use
   `--profile openai-groq-v1` (with `GROQ_API_KEY`) for the previous Groq
   enrichment default. See [model configuration](configuration.md#model-profiles).
3. Run `docker compose --profile ui up -d --build` for the full product, or
   `docker compose up -d --build` for the API and required worker without UI.
4. Check `docker compose ps -a`. Successful `api-init` and `worker-init` jobs
   exit with code 0; the API and worker stay running.

The UI binds loopback port 8086, the direct API port 8000. Override `DI_PORT` or
`DI_API_PORT` in the private `.env` if needed. Datastores are internal to the
Compose network. The UI's API proxy is `/api/knowledge`; direct API requests
use `/v1/...` without that prefix. See the service API guide.

PostgreSQL and Qdrant use named volumes. LocalStack is a disposable local
S3/SQS emulator: restarting it loses original uploads and queued work, even
though indexed text and collection configuration remain. For durable files
and queues, use the [AWS-backed development overlay](deployment/aws.md#dedicated-development-host)
or the supported ECS deployment. The
[AWS guide](deployment/aws.md) describes real S3/SQS and separate API/worker
deployment. Supply HTTPS and secure cookies before exposing any installation.

For a controlled restart, use `docker compose --profile ui stop` followed by
`docker compose --profile ui up -d`. Keep `.env` and named volumes. Do not use
`down -v` as an application rollback. Back up data and encryption/signing
configuration before an upgrade.

The init jobs create current schemas for a fresh database, then seed the three
existing ingestion prompts without overwriting configured prompts. They are
repeatable fresh-install initialization, not retained-database upgrade tools.
For an existing database, review the specific scripts in `backend/migrations/`
with a backup and restore plan; no automatic destructive migration is run.

If an upload remains Processing, inspect `docker compose logs api worker`
privately and verify worker/queue/provider connectivity. Text-based fixtures
need no paid parser. Scanned/layout modes need their configured parser: without
`LLAMA_PARSE_API_KEY` such an upload is refused with HTTP 422 that names the key. Invalid
owner credentials fail explicitly in the standalone profile instead of silently
switching to an installation key. Never paste unredacted logs or credentials
into an issue.
