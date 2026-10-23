# Runtime configuration

`deploy/configure.py` generates an ignored root `.env`. `.env.example` lists
the environment contract; it contains placeholders and emulator credentials.
Real secrets are injected at runtime and excluded from container contexts.

| Setting | Use |
| --- | --- |
| `OPENAI_API_KEY` | Required. Embeddings, enrichment/extraction, retrieval checks and chat in the default profile |
| `GROQ_API_KEY` | Optional. `openai-groq-v1` profile enrichment, or a Groq model chosen per collection |
| `ANTHROPIC_API_KEY` | Optional. Anthropic models chosen per collection or for the optional extraction-validation judge |
| `LLAMA_PARSE_API_KEY` | Optional scanned/layout/visual parsing modes |
| `FIRECRAWL_API_KEY` | Optional configured web scraping |
| `FASTAPI_SECRET_KEY` | Private API master/session signing key; never a public browser key |
| `STANDALONE_PASSWORD`, `STANDALONE_OWNER_EMAIL` | Independent UI sign-in and operator identity |
| `USAGE_CREDENTIALS_MASTER_KEY`, `USAGE_CREDENTIALS_HASH_SALT` | Retained provider-credential encryption and hashing |
| `CONNECTOR_SECRET_KEY` | Optional retained connector-credential encryption |
| `API_KEY_HASH_SECRET` | Retained generated-key pepper when rotating an existing master; default is the master |

## Model profiles

`deploy/configure.py` writes one versioned profile from `deploy/model-profiles.json`
into `.env` and records it as `MODEL_PROFILE`:

| Profile | Keys | Enrichment / extraction | Fallback | Follow-ups, retrieval check |
| --- | --- | --- | --- | --- |
| `openai-v1` (default) | `OPENAI_API_KEY` | `gpt-5.4-mini` | `gpt-5.4-nano` | `gpt-5.4-nano` |
| `openai-groq-v1` | `OPENAI_API_KEY`, `GROQ_API_KEY` | Groq `openai/gpt-oss-120b` | `gpt-5.4-nano` | `gpt-5.4-nano` |

Chat answers use OpenAI `gpt-5.4-mini` unless overridden. Scanned, layout and
visual modes enrich with `gpt-5.6-luna`. New collections embed with OpenAI
`text-embedding-3-small` (`backend/utils/config/knowledge_defaults.py`); a
collection keeps the embedding model it was created with. The optional extraction-validation
LLM judge is off in both profiles. Set `EXTRACTION_VALIDATION_LLM_ENABLED=true`
with an Anthropic key, or point `EXTRACTION_VALIDATION_PROVIDER`/`_MODEL` at another
configured model, to enable it.

Advanced overrides still apply in the existing order: per-collection settings in
the UI/API, then the global admin default, then the environment variables in
`.env`, then code defaults. Edit `.env` directly to change a single node, such as
`RAG_CHAT_ANSWER_LLM_PROVIDER`/`RAG_CHAT_ANSWER_LLM_MODEL`. Profiles change
defaults only. They do not rewrite existing collections or reindex vectors.

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
