# Document Intelligence frontend

The optional static web UI for the complete product in this repository. Start
with the [root installation guide](../docs/installation.md) to run the API,
ingestion worker and UI together. No sibling repositories are required.

For component development, use Bun 1.4.0:

```sh
bun install --frozen-lockfile
bun run format:check
bun run lint
bun run test
bun run build
```

For the local dev server, set `NEXT_PUBLIC_AUTH_MODE=standalone` and
`KNOWLEDGE_API_ORIGIN=http://localhost:8000` in your shell, then `bun run dev`.
Open `/document-intelligence/` on port 3002. The API must be running and
configured for independent authentication. The static export requires the
same-origin API proxy supplied by the standalone nginx configuration.

`bun run preview` serves the built static export. `bun run test:e2e` runs the
stubbed Playwright suite; `bun run test:e2e:live` is an opt-in real-server check.
Install Chromium with `bunx playwright install chromium` first.

Contract generation reads the local API by default. See
[development](../docs/development.md) for source-backed contract checks and
[configuration](../docs/configuration.md) for local versus hosted auth settings.
Private provider and API master credentials must never enter UI configuration.

The static export uses `/document-intelligence/_next/` as its asset prefix.
`deploy/standalone.conf` routes product API and local storage requests when
served by the root Compose stack. See [AWS deployment](../docs/deployment/aws.md)
for static S3/CloudFront hosting and independently deployed API/worker services.
