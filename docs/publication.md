# Publication and source export

This public repository is a clean source snapshot of FutureSmart Document
Intelligence with fresh Git history, licensed Apache-2.0. The private
development repository and its history are retained separately.

## How the snapshot is produced

`python deploy/export_source.py /absolute/output.tar` archives the staged tree
with `git archive`, refuses private paths (`internal/`, `.git`) and real `.env`
files, and writes `docs/source-export.json` with a SHA-256 hash for every file.
Check a checkout against that manifest before trusting it as the reviewed export.

Excluded from the snapshot: private Git history, private product memory and
evidence (`internal/`), and private research records. Public benchmark results
are linked from [the improvement baseline](improvements/baseline.md) rather than
copied.

## Evidence boundaries

Installation and engineering checks show that the application runs and that a
specific behavior holds on the stated inputs. They are not benchmark results,
accuracy scores or load tests. AI Demos results keep their literal published
verdicts; a fix or a green CI run does not change them.

## Retained hosted code

The standalone Compose profile (`MODE=STANDALONE`, `AUTH_MODE=standalone`) does
not call FutureSmart services. Code for FutureSmart's own hosted deployment
remains in the source so the product stays one codebase:

| Category | Where | Standalone behavior |
| --- | --- | --- |
| Hosted sign-in (`auth.futuresmart.ai`) | `backend/utils/auth/dependencies.py`, `frontend/src/platform/config/` | Not used; local operator login |
| Hosted host routing (`app.futuresmart.ai`, `agent.futuresmart.ai`) | `frontend/src/platform/lib/host-routing.ts` | Not used by the standalone UI build |
| Usage metering and provider-credential service | `backend/chat/credentials.py`, `backend/utils/usage/` | Off unless `USAGE_TRACKING_BASE_URL` is set |
| Batch report services (dashboard, chat, NL2SQL) | `workers/services/celery_setup_env.py` | No default endpoint; reports scheduler off |
| Optional voice input (`stt.futuresmart.ai`) | `frontend/src/platform/config/` | Hidden without configuration |
| Default chat-widget icons (public S3 URLs) | `backend/routers/widget_config.py` | Used only by the optional widget |
| Optional chart service and tracing | `CHART_API_URL`, `OTEL_ENDPOINT` | Empty, disabled |

Never point a self-hosted installation at FutureSmart endpoints with your own
keys. Configure your own services, or leave these features off.
