# AWS deployment

The supported production direction is ECS/Fargate with independently deployed
API and ingestion worker tasks. The optional static UI can use private S3 with
CloudFront. Compose is the local installation path; its dummy AWS keys and
LocalStack container are not a hosted deployment configuration.

## Build and configuration

Build from the repository root, tag with an immutable source revision, push to
your private registry and record the resulting digests:

```sh
docker build -f deploy/docker/api.Dockerfile -t "$API_IMAGE" .
docker build -f workers/Dockerfile.celery -t "$WORKER_IMAGE" workers
docker build -f frontend/Dockerfile.standalone -t "$UI_IMAGE" frontend
```

For static S3 hosting, export `/usr/share/nginx/html` from the UI image, preserving
the `document-intelligence/_next` asset directory. Serve the product at
`/document-intelligence/` and route `/api/knowledge/*` to the API without stripping
the prefix. Forward session cookies, disable API caching and allow the existing
long-running chat/stream requests. The standalone UI image uses independent
local login. Optional hosted SSO needs an explicitly configured hosted UI build
and API profile; do not mix public gateway configuration with private API keys.

Provide PostgreSQL, Qdrant, Redis, a private documents S3 bucket and an SQS FIFO
queue. Put API and worker network endpoints in private subnets/security groups.
Use TLS at the external gateway and database/vector-store controls appropriate
to your deployment. Keep the API reachable by the worker's callback URL.

Inject runtime secrets using managed secret references, not Docker build args
or plaintext task-definition environment values. See [configuration](../configuration.md).
Set AWS regions, bucket and queue names/URL, database/vector/Redis endpoints and
`KNOWLEDGE_API_BASE_URL`. The callback URL includes `/api/knowledge`; its private
`KNOWLEDGE_API_KEY` must match the owning API credential. Use `CELERY_BROKER_URL=sqs://`
and task-role credentials. Remove local emulator endpoint settings and dummy
AWS access keys. Grant only the owning bucket/queue operations and required
managed-secret/KMS access. Execution roles and workload roles serve different
purposes; neither belongs in a client application.

Run API schema initialization followed by worker initialization as one-off tasks
only for a **fresh** database, using the same images/configuration as the services:
`python -m scripts.bootstrap_standalone`, then
`python -m services.bootstrap_standalone`. Existing databases need individually
reviewed migration scripts from `backend/migrations/`, backups and rollback.
Preserve existing encryption salts and generated-key HMAC pepper during upgrades.

## Release verification

Record source commit, image digests, configuration/secret references, task
definitions and previous revisions privately. Start the API and required worker,
verify health and queue processing, then verify a real upload, extraction and
cited answer through the UI. Separately stop the frontend and run the direct
service example, matching returned source text/IDs. Restart the new services and
confirm retained collections, indexed text and answers before retiring old tasks.

Rollback means restoring prior task/image/configuration references and UI assets;
it must preserve database, Qdrant, S3 objects and key compatibility. Do not delete
data volumes or regenerate secrets as part of a code rollback. Keep internal
account IDs, exact deployment receipts and operational evidence in your private
runbook rather than in distributable examples.

## Dedicated development host

For durable S3/SQS verification on a dedicated EC2 development host, the optional
`deploy/compose.aws.yaml` overlay keeps PostgreSQL/Qdrant local and replaces the
emulator with your existing private S3 bucket and SQS FIFO queue. This is a
development installation, not an alternative production topology.

Use Compose 2.24.4 or newer. Configure an EC2 workload role restricted to that
bucket/queue, with IMDSv2 and container access (hop limit 2). Do not copy operator
AWS admin credentials into containers. Set bucket CORS GET/PUT/HEAD access for
the browser's exact origin and keep public bucket access blocked. Copy
`deploy/aws.env.example` to ignored root `.env.aws` and fill in your own resource
identifiers. Generate the separate private `.env` once as in the local guide.

From the repository root:

```sh
docker compose --env-file .env --env-file .env.aws -f compose.yaml -f deploy/compose.aws.yaml --profile ui up -d --build
```

Use the same flags for `stop`, `ps` and subsequent `up` commands. Omit `--profile ui`
for an API-only installation; explicitly stop an already-running `ui` service
before service-only verification. Do not switch an existing emulator-backed
database to a different bucket and assume old objects were migrated. Use a fresh
Compose project/database or perform an explicit reviewed data migration.
