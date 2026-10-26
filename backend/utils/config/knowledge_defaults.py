"""Platform defaults for a knowledge collection's configuration.

Why these exist as a named module rather than scattered literals:

A collection used to be unusable until someone sent `PUT /v1/{knowledge_id}/config` with an
embedding model. The row was created with every configuration column `NULL`, and ingestion refused
to run without a model — so the UI was obliged to make a decision it has no basis for making.
Asking a product surface to choose an embedding model is asking the wrong layer: the backend is
where that judgement belongs, and where it can change later without a UI release.

So the API now treats every configuration field as optional and fills in what the caller left out
from the values below. `embedding_model` and the extraction-LLM fields
(`enrichment_provider`/`enrichment_model`/`enrichment_temperature`/`enrichment_prompt`) stay
writable for a caller who *wants* to choose — that's what keeps experimentation through the API
possible. `hybrid_search_enabled`, `use_deterministic_chunking`, and the two `chunking_strategy`
metadata levels are not: they're platform choices with no caller-facing knob at all, applied
purely from the values below (see `routers/knowledge_config_new.py`).

Two of these defaults are not arbitrary and should not be changed casually:

* `EMBEDDING_MODEL` is **write-once per collection** (existing vectors live in one embedding space
  and are meaningless in another), so whatever value is in force when the first document is indexed
  is the value that collection keeps for life. Changing the constant affects only new collections —
  existing ones keep whatever they were pinned to.

  Changed 2026-08-28 from the local sentence-transformer to `openai-text-embedding-3-small`. Two
  consequences follow directly and neither is reversible for a collection once it indexes:

  - **An unconfigured collection now generates spend.** The previous default was chosen precisely
    because it was local, free and needed no third-party key. A new collection now embeds through
    the OpenAI API, billed per token, on the platform key. That property is gone, deliberately.
  - **The estate is split across two vector spaces.** Every collection created before this date
    stays on `paraphrase-multilingual-mpnet-base-v2`; every one after is on the OpenAI model. They
    are not comparable and cannot share vectors, so retrieval quality will differ between old and
    new collections for reasons that have nothing to do with their content. No migration exists —
    re-embedding an existing collection means re-indexing every document in it.
* `DOCUMENT_LEVEL_METADATA = True` matters more than it looks. The worker picks its enrichment
  strategy by testing this flag for truthiness, and `NULL` is falsy — so a collection that never
  received a `PUT` would silently fall through to the weakest strategy (chunking only, with no
  document-level vector at all) rather than the documented default. Leaving the column unset is
  therefore not a neutral act, which is exactly why these are written to the row rather than merely
  applied in memory.
"""

from utils.models.enums import EmbeddingModel

# The embedding model an unconfigured collection is pinned to on first use.
# Billed per token on the platform OpenAI key, and write-once per collection — see the module
# docstring for what changing this constant does and does not affect.
EMBEDDING_MODEL: str = EmbeddingModel.option3.value  # openai-text-embedding-3-small

# Dense + BM25 sparse retrieval. On by default because hybrid search is strictly better on the
# short, keyword-ish queries most collections actually receive.
HYBRID_SEARCH_ENABLED: bool = True

# Extract document-level metadata during ingestion. See the docstring — NULL here is not neutral.
DOCUMENT_LEVEL_METADATA: bool = True

# Chunk-level metadata is off by default: it multiplies LLM cost per document and only pays off for
# collections that actually query on per-chunk fields. Opt in, not out.
CHUNK_LEVEL_METADATA: bool = False

# Deterministic (rule-based, fixed-size/overlap) chunking rather than an LLM-assisted split. On by
# default: it's free, reproducible, and the platform's own choice — not something the UI should
# have to decide.
USE_DETERMINISTIC_CHUNKING: bool = True


# The fields this module is prepared to fill in, and the value used for each. Kept as a dict so the
# initializer can iterate rather than repeating the list of columns, and so a reader can see the
# whole default configuration in one place.
CONFIG_DEFAULTS = {
    "embedding_model": EMBEDDING_MODEL,
    "hybrid_search_enabled": HYBRID_SEARCH_ENABLED,
    "document_level_metadata": DOCUMENT_LEVEL_METADATA,
    "chunk_level_metadata": CHUNK_LEVEL_METADATA,
    "use_deterministic_chunking": USE_DETERMINISTIC_CHUNKING,
}
