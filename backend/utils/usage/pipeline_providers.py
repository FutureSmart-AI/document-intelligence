"""Which external providers a single ingest will actually pay for.

This exists to answer one question precisely: **is this ingest entirely funded by the
owner's own provider keys?** If yes, the platform's ``max_knowledge_items`` ceiling
does not apply — we are not rationing our own spend, because there is none. If no, the
ceiling applies *and* the refusal has to name which providers the owner would need to
add a key for to get the exemption. A "you hit your limit" message that does not say
what to do about it is not actionable.

The provider set depends on the request, not just the account, because different
sources run different stages:

  ============================  ==========================================  ===================================
  Stage                         Runs when                                   Provider
  ============================  ==========================================  ===================================
  Document parsing              a file, and the mode is not ``text_based``   ``llamaparse``
  Audio transcription           the file is audio                           ``openai`` or ``groq``
  Web scrape / crawl            a weblink, and ``scrape_using`` is FireCrawl ``firecrawl``
  Embedding                     always                                      ``openai`` (only for OpenAI models)
  Metadata enrichment           ``enrichment_enabled`` on the collection     the configured enrichment provider
  ============================  ==========================================  ===================================

Two stages deliberately contribute **nothing** to the set:

* **A local embedding model.** The default (``paraphrase-multilingual-mpnet-base-v2``)
  is a sentence-transformer that runs on our own compute with no API key. There is no
  provider to bring a key for, so requiring one would make the exemption unreachable
  for anyone on the recommended default — which would be perverse. Local compute is a
  real cost to us, but it is not *provider* spend, and this rule is about providers.
* **A non-FireCrawl scraper** (``newspaper``, ``langchain``) for the same reason: no
  key, no provider.

Provider names here must match the BYOK whitelist in
``utils/pydantic_models_usage_credentials.py`` exactly — they are looked up against
``owner_provider_keys.provider``. A typo here silently means "never exempt".

Ground truth for the mapping is the worker's own ``get_api_key(owner_id, <provider>, ...)``
call sites, not this docstring:

  * ``celery_utils/processing/file_utils.py:145``            → ``llamaparse``
  * ``celery_utils/processing/file_utils.py:359,366``        → ``openai`` / ``groq`` (audio)
  * ``celery_utils/processing/weblink_utils.py:310,356``     → ``firecrawl``
  * ``celery_utils/indexing/indexer.py:185``                 → ``openai`` (OpenAI embeddings only)
  * ``celery_utils/enrichment/llm_factory.py:348-428``       → the enrichment provider

If a new paid stage is added to the worker, it must be added here too, or an ingest
will be exempted while still costing the platform money.
"""

from __future__ import annotations

from typing import Optional, Set

from utils.logger import logger
from utils.models.enums import DocumentProcessingMode

# Audio MIME prefixes / extensions that route to transcription. Kept deliberately
# broad — a false positive costs a stricter exemption, a false negative would exempt
# an ingest that really does spend on Whisper.
_AUDIO_MIME_PREFIXES = ("audio/", "video/")
_AUDIO_EXTENSIONS = (".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".ogg", ".flac")

# The two transcription backends, keyed by the request's transcript_provider value.
_TRANSCRIPT_PROVIDER_MAP = {"openai": "openai", "groq": "groq"}


def _is_audio(file_name: Optional[str], content_type: Optional[str]) -> bool:
    if content_type and content_type.lower().startswith(_AUDIO_MIME_PREFIXES):
        return True
    if file_name and file_name.lower().endswith(_AUDIO_EXTENSIONS):
        return True
    return False


def _embedding_provider(embedding_model: Optional[str]) -> Optional[str]:
    """``openai`` for an OpenAI embedding model, ``None`` for a local one.

    Mirrors the worker's own branch at ``indexer.py:185``, which only looks up a key
    when the resolved provider is ``openai``; every other type is a local
    sentence-transformer that needs no credential.
    """
    if not embedding_model:
        return None
    try:
        from utils.qdrant.usage_tracking import _get_embedding_provider_and_model

        provider, _ = _get_embedding_provider_and_model(embedding_model)
        return "openai" if provider == "openai" else None
    except Exception as e:
        # Unknown model → assume it might be paid. Being wrong in this direction only
        # means the owner keeps their limit; the other direction gives away free spend.
        logger.warning(f"Could not resolve embedding provider for {embedding_model!r}: {e} — assuming paid")
        return "openai"


def resolve_pipeline_providers(
    *,
    embedding_model: Optional[str],
    enrichment_enabled: bool = True,
    enrichment_provider: Optional[str] = None,
    has_file: bool = False,
    file_name: Optional[str] = None,
    content_type: Optional[str] = None,
    processing_mode: Optional[DocumentProcessingMode] = None,
    transcript_provider: Optional[str] = None,
    has_weblink: bool = False,
    scrape_using: Optional[str] = None,
) -> Set[str]:
    """The set of paid provider names this ingest will touch.

    Every argument is keyword-only on purpose: this takes eight loosely-typed
    optionals, and a positional call site would be unreadable and easy to get subtly
    wrong (swapping ``file_name`` and ``content_type`` would silently break audio
    detection).
    """
    providers: Set[str] = set()

    embed = _embedding_provider(embedding_model)
    if embed:
        providers.add(embed)

    # Enrichment runs per document when enabled, and its provider is per collection.
    # `enrichment_enabled=False` means we index our own metadata with no LLM call.
    if enrichment_enabled and enrichment_provider:
        providers.add(enrichment_provider.strip().lower())

    if has_file:
        if _is_audio(file_name, content_type):
            # Audio goes to transcription, not to LlamaParse — and per the
            # document-processing-modes work, a mode never applies to audio.
            key = (transcript_provider or "openai").strip().lower()
            providers.add(_TRANSCRIPT_PROVIDER_MAP.get(key, "openai"))
        elif processing_mode is not None and processing_mode is not DocumentProcessingMode.TEXT_BASED:
            # Any non-text_based mode routes the file through LlamaParse. text_based
            # uses the free local readers, so it adds no provider.
            providers.add("llamaparse")

    if has_weblink:
        # ScrapeUsing.option2 is FireCrawl (the default). The other scrapers
        # (newspaper, langchain) are library code with no API key.
        scraper = (scrape_using or "").strip().lower()
        if "firecrawl" in scraper:
            providers.add("firecrawl")

    return providers


def missing_byok_providers(pipeline: Set[str], byok: Set[str]) -> Set[str]:
    """Providers this ingest needs that the owner has not supplied a key for.

    Empty result means the whole pipeline is owner-funded and the ingest is exempt
    from the platform's knowledge-item ceiling.
    """
    return set(pipeline) - set(byok)
