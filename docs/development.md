# Development

Use Python 3.11 for backend/worker development and Bun 1.4.0 for the frontend.
The root Compose build is the supported complete installation and keeps the two
Python dependency environments separate.

## Backend and worker

Install `backend/requirements.txt` and `requirements-dev.txt` in a backend virtual
environment. Run API commands from `backend/`, or add `backend/` to `PYTHONPATH`.
For example, from the repository root with that environment selected:

```sh
PYTHONPATH=backend python -m unittest discover -s tests/unit -p test_standalone_install.py -v
```

Use the built API container to avoid host dependency differences:

```sh
docker compose run --rm -v "$PWD/tests:/app/tests:ro" api python -m unittest discover -s tests/unit -p test_standalone_install.py -v
docker compose run --rm -v "$PWD/tests/worker:/app/testing:ro" worker python -m unittest discover -s testing -p test_standalone_credentials.py -v
```

Worker source uses its own `workers/requirements_celery.txt` and working directory.
Its tests live in `tests/worker/` and are mounted at `/app/testing` in CI.
Search-scope regressions use a real Qdrant 1.15.1 server; the embedded client
does not reproduce all server fusion/filter semantics. Product CI starts that
server and runs the bounded scope suite.

`backend/scripts/bootstrap_standalone.py` and
`workers/services/bootstrap_standalone.py` initialize a fresh database and the
existing worker prompts. Existing installations need reviewed upgrade scripts
from `backend/migrations/`; creating metadata is not a schema upgrade strategy.

## Frontend and contract

```sh
cd frontend
bun install --frozen-lockfile
bun run format:check
bun run lint
bun run test
bun run build
```

For a local dev server, set `NEXT_PUBLIC_AUTH_MODE=standalone` and
`KNOWLEDGE_API_ORIGIN=http://localhost:8000`, then run `bun run dev`. The server
forwards `/api/knowledge/` to the API. Set these environment variables using
your shell's syntax; no private provider or service key belongs in the UI.
The complete Compose UI handles the same-origin proxy without a dev server.

`bun run gen:knowledge` reads `http://localhost:8000/openapi.json` by default;
`KNOWLEDGE_OPENAPI_URL` can override it. To check a saved specification:

```sh
bun scripts/gen-knowledge-contract.ts --check --spec /path/to/openapi.json
```

CI exports the contract from the backend image built from this checkout using
`python -m scripts.export_openapi /output/openapi.json`. Hosted auth mode is
selected for that export so optional standalone login routes do not change the
shared product-route contract. CI never fetches a private production schema.

The browser suite uses stubbed API calls and is separate from the real UI
installation verification. Optional fixture-generation dependencies live in
`tests/fixtures/requirements.txt`; small source fixtures are already tracked.

## Research-driven changes

Update the [improvement history](improvements/README.md) with each relevant
change or declined capability. Follow the [maintenance protocol](improvements/MAINTAINING.md);
preserve original verdicts, explicit unknowns, fixture/version identity and
separate implementation from independent benchmark and production acceptance.
Do not copy private research into the public history.
