"""
Enrichment dispatcher: routes documents to the correct enrichment strategy.

Kept separate from enrichment_utils to avoid circular imports
(enrichment_strategies imports from enrichment_utils; this module imports from both).
"""

from typing import Optional

from services.celery_logger import logger
from celery_utils.db.db_utils import get_knowledge_config_sync
from celery_utils.shared.tracing import track_time
from services import celery_setup_env

MODEL_ENRICHMENT = celery_setup_env.ENRICHMENT_MODEL


@track_time("Node: Enrich Document")
def enrich_document(
    text: str,
    knowledge_id: str,
    user_id: Optional[str],
    object_id: str,
    chunk_size: Optional[int] = None,
    block_chunking: bool = False,
    enable_chunking: bool = True,
    owner_id: Optional[str] = None,
    processing_mode=None,
):
    """
    Route a document to the correct enrichment strategy based on KnowledgeConfig toggles.

    Strategy selection:
      doc ON  + chunk ON  + deterministic ON  -> Strategy 2 (pre-split + single LLM call)
      doc ON  + chunk ON  + deterministic OFF -> Strategy 1 (LLM metadata for doc AND chunks)
      doc ON  + chunk OFF                     -> Strategy 6 (LLM doc metadata + recursive chunking)
      doc OFF + chunk OFF                     -> Strategy 7 (recursive chunking only)

    Both Strategy 2 dispatcher and the strategy itself guard against empty chunk configs
    by falling back to Strategy 6.

    ``processing_mode`` is how the document was read (text_based / scanned / layout_aware /
    visual / mixed). It does not change which strategy runs — only which model the strategy
    enriches with, and only when the knowledge base has not named one itself. Defaults to
    None ("not told"), which behaves exactly as before it existed.

    Returns:
        Tuple of (enriched_document_dict, chunks_only_flag)
    """
    from celery_utils.enrichment.enrichment_strategies import (
        enrich_document_strategy_1,
        enrich_document_strategy_2,
        enrich_document_strategy_6,
        enrich_document_strategy_6b,
        enrich_document_strategy_7,
    )
    from celery_utils.enrichment.schema_builder import get_chunk_field_configurations_sqlmodel

    try:
        knowledge_config = get_knowledge_config_sync(knowledge_id)

        if knowledge_config:
            provider = knowledge_config.enrichment_provider or "openai"
            model = knowledge_config.enrichment_model or MODEL_ENRICHMENT
            use_deterministic = bool(getattr(knowledge_config, 'use_deterministic_chunking', False))
            # Default True so existing knowledge bases keep running enrichment unchanged.
            enrichment_enabled = bool(getattr(knowledge_config, 'enrichment_enabled', True))
            logger.info(
                f"Using KnowledgeConfig for knowledge {knowledge_id}: "
                f"doc_metadata={knowledge_config.document_level_metadata}, "
                f"chunk_metadata={knowledge_config.chunk_level_metadata}, "
                f"use_deterministic_chunking={use_deterministic}, "
                f"enrichment_enabled={enrichment_enabled}, "
                f"enrichment_provider={provider}, enrichment_model={model}, "
                # The model logged above is the knowledge base's own setting (or the env
                # default). The mode can still override it inside get_enrichment_config,
                # which logs the model actually used.
                f"processing_mode={getattr(processing_mode, 'value', processing_mode)}"
            )

            # Doc-level metadata requested but enrichment turned OFF: store our own
            # extra_metadata into the filterable document point WITHOUT any LLM call.
            # (Enables metadata filtering / source_id dedup / retrieval at zero LLM cost.)
            if knowledge_config.document_level_metadata and not enrichment_enabled:
                logger.info("Strategy: Chunking + Doc Metadata from extra_metadata only (No LLM enrichment)")
                return enrich_document_strategy_6b(
                    text, knowledge_id, user_id, object_id,
                    chunk_size=chunk_size, enable_chunking=enable_chunking,
                    owner_id=owner_id, knowledge_config=knowledge_config,
                ), False

            if knowledge_config.document_level_metadata and knowledge_config.chunk_level_metadata:
                if use_deterministic:
                    # Guard: Strategy 2 requires chunk configs; fall back to Strategy 6 if absent
                    chunk_configs = get_chunk_field_configurations_sqlmodel(knowledge_id)
                    if not chunk_configs:
                        logger.warning(
                            f"use_deterministic_chunking=True but no chunk field configs for "
                            f"knowledge_id={knowledge_id} — falling back to Strategy 6 (Strategy 2 requires chunk configs)"
                        )
                        return enrich_document_strategy_6(
                            text, knowledge_id, user_id, object_id,
                            chunk_size=chunk_size, enable_chunking=enable_chunking,
                            owner_id=owner_id, knowledge_config=knowledge_config,
                            processing_mode=processing_mode,
                        ), False

                    logger.info("Strategy: Deterministic Chunking + Doc Metadata + Chunk Metadata")
                    return enrich_document_strategy_2(
                        text, knowledge_id, user_id, object_id,
                        chunk_size=chunk_size, enable_chunking=enable_chunking,
                        owner_id=owner_id, knowledge_config=knowledge_config,
                        processing_mode=processing_mode,
                    ), False

                logger.info("Strategy: LLM Chunking + Doc Metadata + Chunk Metadata")
                return enrich_document_strategy_1(
                    text, knowledge_id, user_id, object_id,
                    chunk_size=chunk_size, enable_chunking=enable_chunking,
                    owner_id=owner_id, knowledge_config=knowledge_config,
                    processing_mode=processing_mode,
                ), False

            elif knowledge_config.document_level_metadata and not knowledge_config.chunk_level_metadata:
                logger.info("Strategy: Deterministic Chunking + Doc Metadata")
                return enrich_document_strategy_6(
                    text, knowledge_id, user_id, object_id,
                    chunk_size=chunk_size, enable_chunking=enable_chunking,
                    owner_id=owner_id, knowledge_config=knowledge_config,
                    processing_mode=processing_mode,
                ), False

            else:
                logger.info("Strategy: Deterministic Chunking Only")
                return enrich_document_strategy_7(
                    text, knowledge_id, user_id, object_id,
                    chunk_size=chunk_size, enable_chunking=enable_chunking,
                    owner_id=owner_id,
                ), True

    except Exception as e:
        logger.error(f"Error during enrichment pipeline: {e}")
        raise
