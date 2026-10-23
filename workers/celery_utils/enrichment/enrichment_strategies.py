"""
Enrichment strategy implementations.

Active strategies:
  1 - LLM metadata for document AND chunks (with dynamic Pydantic model)
  6 - LLM doc-level metadata + RecursiveCharacterTextSplitter chunking
  7 - RecursiveCharacterTextSplitter chunking only (no metadata)

Deprecated strategies (2–5) are preserved below as comments for reference.
"""

import json
import os
import time
from typing import Optional

from langsmith import traceable

from services.pydantic_models_db import FieldConfiguration
from celery_utils.enrichment.schema_builder import (
    get_field_configurations_sqlmodel,
    get_chunk_field_configurations_sqlmodel,
    create_dynamic_pydantic_model,
    create_deterministic_chunking_pydantic_model,
)
from celery_utils.enrichment.llm_factory import (
    get_enrichment_llm,
    get_fallback_enrichment_llm,
    get_platform_key_llm,
    get_enrichment_config,
    get_enrichment_prompt_extension,
)
from celery_utils.enrichment.llm_retry import invoke_with_retry
from celery_utils.enrichment.enrichment_utils import (
    serialize,
    get_langsmith_run_tree,
    track_enrichment_usage,
    track_enrichment_health,
    fetch_existing_field_values,
)
from celery_utils.enrichment.prompts import create_enrichment_prompt
from celery_utils.shared.tracing import track_time
from langchain_text_splitters import RecursiveCharacterTextSplitter
from services.celery_logger import logger


# --- Active Strategies ---

@track_time("Node: Enrich Document Strategy 1")
@traceable(name="Enrich Document")
def enrich_document_strategy_1(
    text: str,
    knowledge_id: str,
    user_id: Optional[str],
    object_id: str,
    chunk_size: int = 2000,
    enable_chunking: bool = True,
    owner_id: Optional[str] = None,
    knowledge_config=None,
    processing_mode=None,
):
    """
    Default strategy: Uses LLM to extract document-level metadata AND create
    chunks with their own specific metadata.

    Args:
        enable_chunking: If False, create a single chunk containing the entire document text instead of splitting it.
        chunk_size: Optional target chunk size in characters. When provided, guides the LLM to create chunks of approximately this size.
        knowledge_config: Optional KnowledgeConfig for dynamic provider/model selection.
        processing_mode: How the document was read (text_based, scanned, layout_aware,
            visual, mixed). Anything but text_based enriches with the stronger model —
            see get_enrichment_config.
    """
    logger.info(f"Executing Strategy 1 for knowledge_id: {knowledge_id}, enable_chunking: {enable_chunking}, chunk_size: {chunk_size}")

    # Get enrichment configuration (provider, model, temperature) from knowledge_config or defaults
    provider, model, temperature = get_enrichment_config(knowledge_config, processing_mode)
    logger.info(f"Using enrichment config: provider={provider}, model={model}, temperature={temperature}")

    # Get Chunk Field Configurations and create Pydantic model
    chunk_configs = get_chunk_field_configurations_sqlmodel(knowledge_id)
    chunk_model = create_dynamic_pydantic_model("DynamicChunkModel", {"enable_chunking": enable_chunking, "chunk_size": chunk_size}, chunk_configs)

    # Add chunks field configuration for multiple chunks
    if enable_chunking:
        description = "Break the Document into smaller chunks. Don't modify the content provided in the document. Have array of objects"
        description += f". Aim for approximately {chunk_size} characters per chunk"
    else:
        description = "Keep the Whole Document in single chunk. Don't modify the content provided in the document. Have only one object in list/array"

    chunks_config = FieldConfiguration(
        description=description,
        field_name="chunks",
        field_type="chunks",  # This maps to dynamic chunk model
        is_active=True,
        is_required=True,
        is_custom=False,
        level="document",
    )

    # Get document-level field configurations
    field_configurations = get_field_configurations_sqlmodel(knowledge_id)
    # Append chunks config to field configurations
    field_configurations.append(chunks_config)

    # Create doc model with chunk_type parameter
    DynamicOutputModel = create_dynamic_pydantic_model("DynamicOutputModel", {"enable_chunking": enable_chunking, "chunk_size": chunk_size}, field_configurations, chunk_type=chunk_model)

    llm_instance, enrichment_key_type = get_enrichment_llm(owner_id, provider, model, temperature)
    structured_llm = llm_instance.with_structured_output(DynamicOutputModel, include_raw=True)

    # Build fallback before the hot path — cheaper than constructing mid-retry on API failure
    fallback = get_fallback_enrichment_llm(owner_id, temperature)
    structured_fallback = fallback.with_structured_output(DynamicOutputModel) if fallback else None

    # Same provider/model, paid by the platform key — tried FIRST if the
    # owner's key gets rejected, before the (possibly different-model)
    # fallback above. See llm_retry.py::invoke_with_retry and
    # llm_factory.py::get_platform_key_llm.
    platform_key_fallback = (
        get_platform_key_llm(provider, model, temperature).with_structured_output(DynamicOutputModel)
        if enrichment_key_type == "owner-key" else None
    )

    # Get custom prompt extension from knowledge config
    knowledge_prompt = get_enrichment_prompt_extension(knowledge_config)
    prompt = create_enrichment_prompt(existing_metadata=None, include_chunks=True, knowledge_prompt=knowledge_prompt)

    rt = get_langsmith_run_tree()
    if rt:
        rt.metadata["knowledge_id"] = knowledge_id
        rt.metadata["user_id"] = user_id
        rt.metadata["chunking_strategy"] = "LLM Chunking + Doc Metadata + Chunk Metadata"
        rt.metadata["field_configurations_count"] = len(field_configurations)
        rt.metadata["enrichment_provider"] = provider
        rt.metadata["enrichment_model"] = model

    # Minimum text length below which an empty-chunks result is legitimate
    # (blank or near-blank documents shouldn't be retried for empty chunks).
    _MIN_CHARS_FOR_CHUNKING = 50
    # Sentinel values that indicate a field was never filled by the LLM.
    _EMPTY_SENTINELS: set = {"", "not_found"}

    def _validate_enrichment_response(r) -> Optional[str]:
        """
        Detects two failure modes:
        1. Empty chunks when the text is substantial and chunking is enabled.
        2. All document-level fields containing only empty/placeholder values,
           meaning the LLM produced a structurally valid but content-free response.
        """
        # --- Check 1: empty chunks (skip for blank/short text) ---
        chunks = getattr(r, "chunks", None)
        if enable_chunking and len(text.strip()) >= _MIN_CHARS_FOR_CHUNKING and not chunks:
            return (
                "The chunks list is empty. You must split the document into at least one chunk "
                "and populate all required chunk fields."
            )

        # --- Check 2: all document-level fields are empty/placeholder defaults ---
        has_real_content = False
        for field_name in r.model_fields:
            if field_name == "chunks":
                continue
            value = getattr(r, field_name, None)
            if isinstance(value, str) and value not in _EMPTY_SENTINELS:
                has_real_content = True
                break
            if isinstance(value, bool):  # bool is subclass of int — check first
                has_real_content = True
                break
            if isinstance(value, (int, float)):
                has_real_content = True
                break
            if isinstance(value, dict) and value:
                has_real_content = True
                break
            # Sentinel check only applies to strings; dicts/models are always real content.
            # Doing `item not in set` on a dict raises TypeError (unhashable).
            if isinstance(value, list) and any(
                (item not in _EMPTY_SENTINELS) if isinstance(item, str) else True
                for item in value
            ):
                has_real_content = True
                break
        if not has_real_content:
            return (
                "All document-level fields are empty or contain placeholder values. "
                "Extract real metadata from the document content."
            )

        return None

    enrichment_start_time = time.time()
    response, token_usage, exec_meta = invoke_with_retry(
        structured_llm,
        [{"role": "system", "content": prompt}, {"role": "user", "content": text}],
        fallback=structured_fallback,
        response_validator=_validate_enrichment_response,
        primary_model=model,
        primary_provider=provider,
        owner_id=owner_id,
        key_type=enrichment_key_type,
        platform_key_fallback=platform_key_fallback,
    )
    enrichment_duration_ms = int((time.time() - enrichment_start_time) * 1000)

    if response is None:
        # Report the failure before raising. Without this the attempt is
        # invisible to every table we have — track_enrichment_usage below is
        # never reached, so a model that fails often reads as cheap and fast on
        # the analytics screen. exec_meta carries the category (validation vs
        # infrastructure) and the detail.
        track_enrichment_health(
            knowledge_id, object_id,
            status=(exec_meta.get("failure_category") or "error"),
            duration_ms=enrichment_duration_ms,
            enrichment_strategy=1,
            provider=provider, model=model,
            exec_meta=exec_meta, token_usage=token_usage,
            response=None, field_configurations=field_configurations,
            text=text, chunk_count=0,
            owner_id=owner_id, key_type=enrichment_key_type,
            processing_mode=processing_mode,
        )
        raise RuntimeError(f"Enrichment failed for knowledge_id={knowledge_id}, model={model}")

    if exec_meta["used_fallback"]:
        logger.info(f"Fallback model used for enrichment: provider={exec_meta['provider']}, model={exec_meta['model']}")

    # REMOVED: extracted_metadata write path — table being phased out; payload lives in Qdrant.

    # Analytics event (outcome + coverage + latency) — separate from the usage
    # event below, which meters tokens against the monthly limit.
    track_enrichment_health(
        knowledge_id, object_id,
        status="success",
        duration_ms=enrichment_duration_ms,
        enrichment_strategy=1,
        provider=provider, model=model,
        exec_meta=exec_meta, token_usage=token_usage,
        response=response, field_configurations=field_configurations,
        # Was hardcoded 0, which made the scorecard's usage.total_chunks read 0 for
        # every document no matter how many chunks were actually written. Only the
        # SUCCESS path changes: on the failure path response is None and 0 is the
        # truthful count.
        text=text, chunk_count=len(getattr(response, "chunks", None) or []),
        owner_id=owner_id, key_type=enrichment_key_type,
        processing_mode=processing_mode,
    )

    # Send enrichment usage tracking event with actual provider/model (fallback-aware)
    track_enrichment_usage(
        knowledge_id, object_id,
        enrichment_strategy=1,
        token_usage=token_usage,
        duration_ms=enrichment_duration_ms,
        key_type=enrichment_key_type,
        provider=exec_meta["provider"],
        model=exec_meta["model"],
    )

    logger.info(f"Serialized response: {serialize(response)}")
    return serialize(response)


@track_time("Node: Enrich Document Strategy 2")
@traceable(name="Enrich Document")
def enrich_document_strategy_2(
    text: str,
    knowledge_id: str,
    user_id: Optional[str],
    object_id: str,
    chunk_size: int = 2000,
    enable_chunking: bool = True,
    owner_id: Optional[str] = None,
    knowledge_config=None,
    processing_mode=None,
):
    """
    Pre-split + single-call hierarchical enrichment: doc-level + chunk-level metadata.

    Splits text deterministically via RecursiveCharacterTextSplitter, then extracts
    doc-level and chunk-level metadata in ONE LLM call. LLM receives a JSON array of
    chunks keyed by chunk_idx and returns metadata only (no chunk_text). Code assembles
    final chunks by sorting on chunk_idx and writing canonical indices from enumerate.

    Falls back to Strategy 6 internally if no chunk field configs exist.

    Workflow:
    1. Guard: fallback to Strategy 6 if chunk configs empty
    2. Pre-split text → [{"chunk_idx": i, "chunk_text": c}, ...]
    3. Token budget check → strip overlap if oversized
    4. Single LLM call: JSON array input, metadata-only structured output
    5. Validate: len == expected_count, all chunk_idx present; retry with missing values
    6. Assembly: sort by chunk_idx, enumerate for canonical index, zip chunk_text from splitter
    7. Return: doc fields + enriched chunks list

    Args:
        text: Raw document text
        knowledge_id: Knowledge base ID for field configs and token budget config
        user_id: User ID for tracking
        object_id: Object ID for usage tracking
        chunk_size: Target chunk size in characters (default 2000)
        enable_chunking: If False, wraps entire text as single chunk (chunk_idx=0)
        owner_id: Owner ID for LLM factory/provider selection
        knowledge_config: Optional KnowledgeConfig for provider/model/temperature/token_budget

    Returns:
        dict: doc-level metadata fields + "chunks" list of
              {chunk_text (from splitter), chunk_idx (canonical from enumerate), ...chunk_metadata_fields}
    """
    logger.info(
        f"Executing Strategy 2 for knowledge_id={knowledge_id}, "
        f"enable_chunking={enable_chunking}, chunk_size={chunk_size}"
    )

    # ── Guard: fall back to Strategy 6 if no chunk field configs ──────────
    chunk_configs = get_chunk_field_configurations_sqlmodel(knowledge_id)
    if not chunk_configs:
        logger.warning(
            f"No chunk field configs for knowledge_id={knowledge_id} — "
            "falling back to Strategy 6"
        )
        return enrich_document_strategy_6(
            text, knowledge_id, user_id, object_id,
            chunk_size=chunk_size, enable_chunking=enable_chunking,
            owner_id=owner_id, knowledge_config=knowledge_config,
            processing_mode=processing_mode,
        )

    # ── Enrichment config ──────────────────────────────────────────────────
    provider, model, temperature = get_enrichment_config(knowledge_config, processing_mode)
    logger.info(f"Using enrichment config: provider={provider}, model={model}, temperature={temperature}")

    # ── Pre-split ──────────────────────────────────────────────────────────
    if not enable_chunking:
        logger.info("Strategy 2: single-chunk mode (enable_chunking=False)")
        splitter_chunks = [text]
    else:
        if not chunk_size:
            chunk_size = int(os.getenv("CHUNK_SIZE", 2000))

        overlap = max(0, int(chunk_size * 0.1))
        text_splitter = RecursiveCharacterTextSplitter(
            separators=['\n\n', '\n', '.', ',', ' '],
            chunk_size=chunk_size,
            chunk_overlap=overlap,
        )
        splitter_chunks = text_splitter.split_text(text)
        logger.info(f"RecursiveCharacterTextSplitter produced {len(splitter_chunks)} chunks (overlap={overlap})")

    expected_count = len(splitter_chunks)

    # ── Token budget check ─────────────────────────────────────────────────
    # Rough estimate: 4 chars ≈ 1 token; budget threshold from KnowledgeConfig or default 90% of 128k
    _DEFAULT_TOKEN_BUDGET = int(128_000 * 0.9)
    token_budget = _DEFAULT_TOKEN_BUDGET
    if knowledge_config and getattr(knowledge_config, 'token_budget_threshold', None):
        token_budget = knowledge_config.token_budget_threshold

    chunks_payload = [{"chunk_idx": i, "chunk_text": c} for i, c in enumerate(splitter_chunks)]
    serialized_chunks = json.dumps(chunks_payload, ensure_ascii=False)
    estimated_tokens = len(serialized_chunks) / 4

    if estimated_tokens > token_budget and enable_chunking:
        logger.warning(
            f"Token budget exceeded (estimated={int(estimated_tokens)}, budget={token_budget}) — "
            "re-splitting without overlap"
        )
        text_splitter_no_overlap = RecursiveCharacterTextSplitter(
            separators=['\n\n', '\n', '.', ',', ' '],
            chunk_size=chunk_size,
            chunk_overlap=0,
        )
        splitter_chunks = text_splitter_no_overlap.split_text(text)
        expected_count = len(splitter_chunks)
        chunks_payload = [{"chunk_idx": i, "chunk_text": c} for i, c in enumerate(splitter_chunks)]
        serialized_chunks = json.dumps(chunks_payload, ensure_ascii=False)
        logger.info(f"Re-split without overlap: {expected_count} chunks")

    # ── Build output schema ────────────────────────────────────────────────
    doc_configs = get_field_configurations_sqlmodel(knowledge_id)
    DeterministicOutputModel = create_deterministic_chunking_pydantic_model(knowledge_id, doc_configs, chunk_configs)

    # ── LLM setup ─────────────────────────────────────────────────────────
    llm_instance, enrichment_key_type = get_enrichment_llm(owner_id, provider, model, temperature)
    structured_llm = llm_instance.with_structured_output(DeterministicOutputModel, include_raw=True)

    fallback = get_fallback_enrichment_llm(owner_id, temperature)
    structured_fallback = fallback.with_structured_output(DeterministicOutputModel) if fallback else None

    # Same provider/model, paid by the platform key — tried FIRST if the
    # owner's key gets rejected, before the (possibly different-model)
    # fallback above. See llm_retry.py::invoke_with_retry and
    # llm_factory.py::get_platform_key_llm.
    platform_key_fallback = (
        get_platform_key_llm(provider, model, temperature).with_structured_output(DeterministicOutputModel)
        if enrichment_key_type == "owner-key" else None
    )

    knowledge_prompt = get_enrichment_prompt_extension(knowledge_config)

    # ── Fetch existing values for consistency grounding ────────────────────
    # Free-text / unique-per-doc fields: no consistency value, API returns 404 for them
    _CONSISTENCY_FETCH_BLOCKLIST = {
        "summary", "description", "title", "chunk_text",  # free-text, unique per doc
        "document_outline", "temporal_context", "keywords",  # high-cardinality, no consistency value
    }
    all_doc_field_names = [
        c.field_name for c in doc_configs
        if c.field_name and c.field_name not in _CONSISTENCY_FETCH_BLOCKLIST
    ]
    existing_field_values = fetch_existing_field_values(
        knowledge_id=knowledge_id,
        fields=all_doc_field_names,
        limit=50,
    )
    logger.info(
        f"[context_fetch] Existing field values for knowledge_id={knowledge_id}: "
        f"{list(existing_field_values.keys())}"
    )

    prompt = create_enrichment_prompt(
        existing_metadata=None,
        include_chunks=True,
        knowledge_prompt=knowledge_prompt,
        include_deterministic_chunks=True,
        existing_field_values=existing_field_values or None,
    )

    # ── LangSmith metadata ─────────────────────────────────────────────────
    rt = get_langsmith_run_tree()
    if rt:
        rt.metadata["knowledge_id"] = knowledge_id
        rt.metadata["user_id"] = user_id
        rt.metadata["chunking_strategy"] = "Deterministic Chunking + Doc Metadata + Chunk Metadata"
        rt.metadata["expected_chunk_count"] = expected_count
        rt.metadata["enrichment_provider"] = provider
        rt.metadata["enrichment_model"] = model

    # ── Validator ─────────────────────────────────────────────────────────
    _EMPTY_SENTINELS: set = {"", "not_found"}

    def _validate_deterministic_response(r) -> Optional[str]:
        """
        Validates two conditions:
        1. Exact count match: len(chunks_metadata) == expected_count
        2. All chunk_idx values 0..expected_count-1 are present (no drops/dupes)
        3. At least one doc-level field has real content
        """
        chunks_meta = getattr(r, "chunks_metadata", None) or []
        actual_count = len(chunks_meta)

        if actual_count != expected_count:
            present = {m.chunk_idx for m in chunks_meta}
            missing = sorted(set(range(expected_count)) - present)
            return (
                f"Expected {expected_count} chunk metadata entries, got {actual_count}. "
                f"Missing chunk_idx values: {missing}. "
                "Return exactly one entry per input chunk."
            )

        present_indices = {m.chunk_idx for m in chunks_meta}
        expected_indices = set(range(expected_count))
        if present_indices != expected_indices:
            missing = sorted(expected_indices - present_indices)
            return (
                f"chunk_idx values are incorrect. "
                f"Missing: {missing}. "
                "Each entry must have a chunk_idx matching its input position."
            )

        # Check at least one doc-level field has real content
        has_real_content = False
        for field_name in r.model_fields:
            if field_name == "chunks_metadata":
                continue
            value = getattr(r, field_name, None)
            if isinstance(value, str) and value not in _EMPTY_SENTINELS:
                has_real_content = True
                break
            if isinstance(value, bool):
                has_real_content = True
                break
            if isinstance(value, (int, float)):
                has_real_content = True
                break
            if isinstance(value, dict) and value:
                has_real_content = True
                break
            if isinstance(value, list) and any(
                (item not in _EMPTY_SENTINELS) if isinstance(item, str) else True
                for item in value
            ):
                has_real_content = True
                break
        if not has_real_content:
            return (
                "All document-level fields are empty or contain placeholder values. "
                "Extract real metadata from the document content."
            )

        return None

    # ── Invoke ─────────────────────────────────────────────────────────────
    enrichment_start_time = time.time()
    response, token_usage, exec_meta = invoke_with_retry(
        structured_llm,
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": serialized_chunks},
        ],
        fallback=structured_fallback,
        response_validator=_validate_deterministic_response,
        primary_model=model,
        primary_provider=provider,
        owner_id=owner_id,
        key_type=enrichment_key_type,
        platform_key_fallback=platform_key_fallback,
    )
    enrichment_duration_ms = int((time.time() - enrichment_start_time) * 1000)

    if response is None:
        # Report the failure before raising. Without this the attempt is
        # invisible to every table we have — track_enrichment_usage below is
        # never reached, so a model that fails often reads as cheap and fast on
        # the analytics screen. exec_meta carries the category (validation vs
        # infrastructure) and the detail.
        track_enrichment_health(
            knowledge_id, object_id,
            status=(exec_meta.get("failure_category") or "error"),
            duration_ms=enrichment_duration_ms,
            enrichment_strategy=2,
            provider=provider, model=model,
            exec_meta=exec_meta, token_usage=token_usage,
            response=None, field_configurations=None,
            text=text, chunk_count=expected_count,
            owner_id=owner_id, key_type=enrichment_key_type,
            processing_mode=processing_mode,
        )
        raise RuntimeError(f"Strategy 2 enrichment failed for knowledge_id={knowledge_id}, model={model}")

    if exec_meta["used_fallback"]:
        logger.info(
            f"Fallback model used for Strategy 2: "
            f"provider={exec_meta['provider']}, model={exec_meta['model']}"
        )

    # Analytics event (outcome + coverage + latency) — separate from the usage
    # event below, which meters tokens against the monthly limit.
    track_enrichment_health(
        knowledge_id, object_id,
        status="success",
        duration_ms=enrichment_duration_ms,
        enrichment_strategy=2,
        provider=provider, model=model,
        exec_meta=exec_meta, token_usage=token_usage,
        response=response, field_configurations=None,
        text=text, chunk_count=expected_count,
        owner_id=owner_id, key_type=enrichment_key_type,
        processing_mode=processing_mode,
    )

    track_enrichment_usage(
        knowledge_id, object_id,
        enrichment_strategy=2,
        token_usage=token_usage,
        duration_ms=enrichment_duration_ms,
        key_type=enrichment_key_type,
        provider=exec_meta["provider"],
        model=exec_meta["model"],
    )

    # ── Assembly ───────────────────────────────────────────────────────────
    # Sort by LLM-returned chunk_idx for alignment, then write canonical index from enumerate.
    sorted_meta = sorted(response.chunks_metadata, key=lambda m: m.chunk_idx)
    chunks = []
    for i, meta in enumerate(sorted_meta):
        # mode="json" so a chunk-level `date` field lands as "YYYY-MM-DD".
        # These chunk dicts are assigned onto response_dict AFTER serialize()
        # has run over the response, so they never pass through it — a live
        # datetime.date here would reach the JSON payload unconverted.
        meta_dict = meta.model_dump(mode="json", exclude={"chunk_idx"})
        chunks.append({"chunk_text": splitter_chunks[i], "chunk_idx": i, **meta_dict})

    # Build final response dict: all doc-level fields + assembled chunks
    response_dict = serialize(response)
    response_dict.pop("chunks_metadata", None)
    response_dict["chunks"] = chunks

    logger.info(
        f"Strategy 2 complete: {len(chunks)} chunks assembled for knowledge_id={knowledge_id}"
    )
    return response_dict


@track_time("Node: Enrich Document Strategy 6")
@traceable(name="Enrich Document")
def enrich_document_strategy_6(
    text: str,
    knowledge_id: str,
    user_id: Optional[str],
    object_id: str,
    chunk_size: int = 2000,
    enable_chunking: bool = True,
    owner_id: Optional[str] = None,
    knowledge_config=None,
    processing_mode=None,
):
    """
    Strategy 6: Uses LLM for document-level metadata ONLY, then uses RecursiveCharacterTextSplitter
    for chunking. No chunk-level metadata is generated.
    This strategy is used when document_level_metadata=True and chunk_level_metadata=False.

    Args:
        enable_chunking: If False, create a single chunk containing the entire document text instead of splitting it.
        knowledge_config: Optional KnowledgeConfig for dynamic provider/model selection.
        processing_mode: How the document was read (text_based, scanned, layout_aware,
            visual, mixed). Anything but text_based enriches with the stronger model —
            see get_enrichment_config.
    """
    logger.info(f"Executing Strategy 6 (Doc Metadata + Recursive Chunking) for knowledge_id: {knowledge_id}, enable_chunking: {enable_chunking}")

    # Get enrichment configuration (provider, model, temperature) from knowledge_config or defaults
    provider, model, temperature = get_enrichment_config(knowledge_config, processing_mode)
    logger.info(f"Using enrichment config: provider={provider}, model={model}, temperature={temperature}")

    # Step 1: Extract document-level metadata using LLM
    field_configurations = get_field_configurations_sqlmodel(knowledge_id)

    DynamicOutputModel = create_dynamic_pydantic_model("DynamicOutputModel", {"enable_chunking": enable_chunking, "chunk_size": chunk_size}, field_configurations)

    # Create LLM instance using factory (supports OpenAI, Groq, Anthropic)
    llm_instance, enrichment_key_type = get_enrichment_llm(owner_id, provider, model, temperature)
    structured_llm = llm_instance.with_structured_output(DynamicOutputModel, include_raw=True)

    # Build fallback before the hot path — cheaper than constructing mid-retry on API failure
    fallback = get_fallback_enrichment_llm(owner_id, temperature)
    structured_fallback = fallback.with_structured_output(DynamicOutputModel) if fallback else None

    # Same provider/model, paid by the platform key — tried FIRST if the
    # owner's key gets rejected, before the (possibly different-model)
    # fallback above. See llm_retry.py::invoke_with_retry and
    # llm_factory.py::get_platform_key_llm.
    platform_key_fallback = (
        get_platform_key_llm(provider, model, temperature).with_structured_output(DynamicOutputModel)
        if enrichment_key_type == "owner-key" else None
    )

    # Get custom prompt extension from knowledge config
    knowledge_prompt = get_enrichment_prompt_extension(knowledge_config)
    prompt = create_enrichment_prompt(existing_metadata=None, include_chunks=False, knowledge_prompt=knowledge_prompt)

    rt = get_langsmith_run_tree()
    if rt:
        rt.metadata["knowledge_id"] = knowledge_id
        rt.metadata["user_id"] = user_id
        rt.metadata["chunking_strategy"] = "Deterministic Chunking + Doc Metadata"
        rt.metadata["field_configurations_count"] = len(field_configurations)
        rt.metadata["enrichment_provider"] = provider
        rt.metadata["enrichment_model"] = model

    _EMPTY_SENTINELS: set = {"", "not_found"}

    def _validate_doc_fields(r) -> Optional[str]:
        """Detect all-empty doc-level response — no chunk fields exist in this schema."""
        has_real_content = False
        for field_name in r.model_fields:
            value = getattr(r, field_name, None)
            if isinstance(value, str) and value not in _EMPTY_SENTINELS:
                has_real_content = True
                break
            if isinstance(value, bool):  # bool is subclass of int — check first
                has_real_content = True
                break
            if isinstance(value, (int, float)):
                has_real_content = True
                break
            if isinstance(value, dict) and value:
                has_real_content = True
                break
            # Sentinel check only applies to strings; dicts/models are always real content.
            # Doing `item not in set` on a dict raises TypeError (unhashable).
            if isinstance(value, list) and any(
                (item not in _EMPTY_SENTINELS) if isinstance(item, str) else True
                for item in value
            ):
                has_real_content = True
                break
        if not has_real_content:
            return (
                "All document-level fields are empty or contain placeholder values. "
                "Extract real metadata from the document content (title, summary, keywords, topics, etc.)."
            )
        return None

    enrichment_start_time = time.time()
    response, token_usage, exec_meta = invoke_with_retry(
        structured_llm,
        [{"role": "system", "content": prompt}, {"role": "user", "content": text}],
        fallback=structured_fallback,
        response_validator=_validate_doc_fields,
        primary_model=model,
        primary_provider=provider,
        owner_id=owner_id,
        key_type=enrichment_key_type,
        platform_key_fallback=platform_key_fallback,
    )
    enrichment_duration_ms = int((time.time() - enrichment_start_time) * 1000)

    if response is None:
        # Report the failure before raising. Without this the attempt is
        # invisible to every table we have — track_enrichment_usage below is
        # never reached, so a model that fails often reads as cheap and fast on
        # the analytics screen. exec_meta carries the category (validation vs
        # infrastructure) and the detail.
        track_enrichment_health(
            knowledge_id, object_id,
            status=(exec_meta.get("failure_category") or "error"),
            duration_ms=enrichment_duration_ms,
            enrichment_strategy=6,
            provider=provider, model=model,
            exec_meta=exec_meta, token_usage=token_usage,
            response=None, field_configurations=field_configurations,
            text=text, chunk_count=0,
            owner_id=owner_id, key_type=enrichment_key_type,
            processing_mode=processing_mode,
        )
        raise RuntimeError(f"Enrichment failed for knowledge_id={knowledge_id}, model={model}")

    if exec_meta["used_fallback"]:
        logger.info(f"Fallback model used for enrichment: provider={exec_meta['provider']}, model={exec_meta['model']}")

    # REMOVED: extracted_metadata write path — table being phased out; payload lives in Qdrant.

    # Analytics event (outcome + coverage + latency) — separate from the usage
    # event below, which meters tokens against the monthly limit.
    track_enrichment_health(
        knowledge_id, object_id,
        status="success",
        duration_ms=enrichment_duration_ms,
        enrichment_strategy=6,
        provider=provider, model=model,
        exec_meta=exec_meta, token_usage=token_usage,
        response=response, field_configurations=field_configurations,
        text=text, chunk_count=0,
        owner_id=owner_id, key_type=enrichment_key_type,
        processing_mode=processing_mode,
    )

    # Send enrichment usage tracking event with actual provider/model (fallback-aware)
    track_enrichment_usage(
        knowledge_id, object_id,
        enrichment_strategy=6,
        token_usage=token_usage,
        duration_ms=enrichment_duration_ms,
        key_type=enrichment_key_type,
        provider=exec_meta["provider"],
        model=exec_meta["model"],
    )

    # Step 2: Perform recursive text splitting (skip if chunking is blocked)
    if not enable_chunking:
        logger.info("Creating single chunk for document (enable_chunking=False)")
        chunks_text = [text]
        logger.info(f"Created single chunk with {len(text)} characters")
    else:
        logger.info("Performing recursive character text splitting...")
        if not chunk_size:
            chunk_size = int(os.getenv("CHUNK_SIZE", 2000))

        text_splitter = RecursiveCharacterTextSplitter(
            separators=['\n\n', '\n', '.', ',', ' '],
            chunk_size=chunk_size,
            chunk_overlap=max(0, int(chunk_size * 0.1))
        )
        chunks_text = text_splitter.split_text(text)
        logger.info(f"RecursiveCharacterTextSplitter created {len(chunks_text)} chunks.")

    # Step 3: Combine results into the standard format
    response_dict = serialize(response)
    response_dict["chunks"] = [{"chunk_text": chunk} for chunk in chunks_text]

    return response_dict


@track_time("Node: Enrich Document Strategy 7")
@traceable(name="Enrich Document")
def enrich_document_strategy_7(
    text: str,
    knowledge_id: str,
    user_id: Optional[str],
    object_id: str,
    chunk_size: int = 2000,
    enable_chunking: bool = True,
    owner_id: Optional[str] = None,
):
    """
    Strategy 7: Uses RecursiveCharacterTextSplitter ONLY for chunking.
    No document-level or chunk-level metadata is generated.
    This strategy is used when both document_level_metadata=False and chunk_level_metadata=False.

    Args:
        enable_chunking: If False, create a single chunk containing the entire document text instead of splitting it.
    """
    logger.info(f"Executing Strategy 7 (Recursive Chunking Only (No Metadata)) for knowledge_id: {knowledge_id}, enable_chunking: {enable_chunking}")

    rt = get_langsmith_run_tree()
    if rt:
        rt.metadata["knowledge_id"] = knowledge_id
        rt.metadata["user_id"] = user_id
        rt.metadata["chunking_strategy"] = "Deterministic Chunking Only"

    if not enable_chunking:
        logger.info("Creating single chunk for document (enable_chunking=False)")
        chunks_text = [text]
        logger.info(f"Created single chunk with {len(text)} characters")
    else:
        logger.info("Performing recursive character text splitting...")
        if not chunk_size:
            chunk_size = int(os.getenv("CHUNK_SIZE", 2000))

        text_splitter = RecursiveCharacterTextSplitter(
            separators=['\n\n', '\n', '.', ',', ' '],
            chunk_size=chunk_size,
            chunk_overlap=max(0, int(chunk_size * 0.1))
        )
        chunks_text = text_splitter.split_text(text)
        logger.info(f"RecursiveCharacterTextSplitter created {len(chunks_text)} chunks.")

    response_dict = {}
    response_dict["chunks"] = [{"chunk_text": chunk} for chunk in chunks_text]

    return response_dict


@track_time("Node: Enrich Document Strategy 6b")
@traceable(name="Enrich Document")
def enrich_document_strategy_6b(
    text: str,
    knowledge_id: str,
    user_id: Optional[str],
    object_id: str,
    chunk_size: int = 2000,
    enable_chunking: bool = True,
    owner_id: Optional[str] = None,
    knowledge_config=None,
):
    """
    Strategy 6b: Recursive chunking + a document-level point built ONLY from the
    caller-provided extra_metadata — NO LLM enrichment call.

    Used when document_level_metadata=True, chunk_level_metadata=False, and
    enrichment_enabled=False. The dispatcher returns chunks_only=False for this
    strategy so index_in_vectordb writes the document point (system fields + our
    extra_metadata), which is what makes metadata filtering, source_id dedup and
    per-document metadata retrieval work — all without any LLM cost.

    A truncated copy of the text is returned as `summary` purely so the document
    point has a non-empty vector to embed (the LLM-derived fields are intentionally
    absent).
    """
    logger.info(
        f"Executing Strategy 6b (Chunking + Doc Metadata from extra_metadata only, NO LLM) "
        f"for knowledge_id: {knowledge_id}, enable_chunking: {enable_chunking}"
    )

    rt = get_langsmith_run_tree()
    if rt:
        rt.metadata["knowledge_id"] = knowledge_id
        rt.metadata["user_id"] = user_id
        rt.metadata["chunking_strategy"] = "Deterministic Chunking + Doc Metadata (No Enrichment)"
        rt.metadata["enrichment_provider"] = "none"
        rt.metadata["enrichment_model"] = "none"

    if not enable_chunking:
        logger.info("Creating single chunk for document (enable_chunking=False)")
        chunks_text = [text]
    else:
        logger.info("Performing recursive character text splitting...")
        if not chunk_size:
            chunk_size = int(os.getenv("CHUNK_SIZE", 2000))

        text_splitter = RecursiveCharacterTextSplitter(
            separators=['\n\n', '\n', '.', ',', ' '],
            chunk_size=chunk_size,
            chunk_overlap=max(0, int(chunk_size * 0.1))
        )
        chunks_text = text_splitter.split_text(text)
        logger.info(f"RecursiveCharacterTextSplitter created {len(chunks_text)} chunks.")

    # No LLM fields. `summary` gives the document point a non-empty vector to embed.
    response_dict = {"summary": (text or "")[:2000]}
    response_dict["chunks"] = [{"chunk_text": chunk} for chunk in chunks_text]

    return response_dict


# --- Deprecated Strategies (preserved for reference) ---

# # Deprecated strategy
# @traceable(name="Enrich Document")
# def enrich_document_strategy_2(text: str, knowledge_id: str, user_id: Optional[str], object_id: str):
#     """
#     Strategy 2: Uses LLM to extract document-level metadata and create chunks,
#     but does NOT extract metadata for the individual chunks.
#     """
#     logger.info(f"Executing Strategy 2 for knowledge_id: {knowledge_id}")
#
#     field_configurations = get_field_configurations_sqlmodel(knowledge_id)
#
#     # Add chunks field configuration for multiple chunks (without metadata)
#     chunks_config = FieldConfiguration(
#         field_name="chunks",
#         field_type="chunks",  # This maps to List[ChunkText]
#         is_active=True,
#         level="document",
#         knowledge_id=knowledge_id
#     )
#
#     field_configurations.append(chunks_config)
#
#     DynamicOutputModel = create_dynamic_pydantic_model("DynamicOutputModel", "LLM_CHUNK_NO_METADATA", field_configurations)
#     structured_llm = model_enrichment.with_structured_output(DynamicOutputModel, include_raw=True)
#
#     prompt = create_enrichment_prompt(existing_metadata=None, include_chunks=True)
#
#     rt = get_langsmith_run_tree()
#     if rt:
#         rt.metadata["knowledge_id"] = knowledge_id
#         rt.metadata["user_id"] = user_id
#         rt.metadata["chunking_strategy"] = "LLM_CHUNK_NO_METADATA"
#         rt.metadata["field_configurations_count"] = len(field_configurations)
#
#     response = invoke_with_retry(structured_llm, [
#         {"role": "system", "content": prompt},
#         {"role": "user", "content": text}
#     ])
#
#     return serialize(response)


# # Deprecated strategy
# @traceable(name="Enrich Document")
# def enrich_document_strategy_3(text: str, knowledge_id: str, user_id: Optional[str], object_id: str):
#     """
#     Strategy 3: Uses LLM for document-level metadata, then uses LangChain's
#     SemanticChunker to split the text. No chunk-level metadata is generated.
#     """
#     logger.info(f"Executing Strategy 3 for knowledge_id: {knowledge_id}")
#
#     field_configurations = get_field_configurations_sqlmodel(knowledge_id)
#     DynamicOutputModel = create_dynamic_pydantic_model("DynamicOutputModel", "SEMANTIC_CHUNK", field_configurations)
#     structured_llm = model_enrichment.with_structured_output(DynamicOutputModel, include_raw=True)
#
#     prompt = create_enrichment_prompt(existing_metadata=None, include_chunks=False)
#
#     rt = get_langsmith_run_tree()
#     if rt:
#         rt.metadata["knowledge_id"] = knowledge_id
#         rt.metadata["user_id"] = user_id
#         rt.metadata["chunking_strategy"] = "SEMANTIC_CHUNK"
#         rt.metadata["field_configurations_count"] = len(field_configurations)
#
#     response = invoke_with_retry(structured_llm, [
#         {"role": "system", "content": prompt},
#         {"role": "user", "content": text}
#     ])
#
#     # Step 2: Perform semantic chunking
#     # embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
#     # text_splitter = SemanticChunker(embeddings)
#     # chunks_text = text_splitter.split_text(text)
#
#     response_dict = serialize(response)
#     # response_dict["chunks"] = [{"chunk_text": chunk} for chunk in chunks_text]
#     return response_dict


# # Deprecated strategy
# @traceable(name="Enrich Document")
# def enrich_document_strategy_4(text: str, knowledge_id: str, user_id: Optional[str], object_id: str):
#     """
#     Strategy 4: Uses LLM for document-level metadata and to determine character
#     breakpoints for chunks. The text is then sliced locally. No chunk-level metadata.
#     """
#     logger.info(f"Executing Strategy 4 for knowledge_id: {knowledge_id}")
#
#     field_configurations = get_field_configurations_sqlmodel(knowledge_id)
#
#     chunks_config = FieldConfiguration(
#         field_name="chunks",
#         field_type="chunks",  # This maps to List[ChunkWithBreakpoint]
#         is_active=True,
#         level="document",
#         knowledge_id=knowledge_id
#     )
#
#     field_configurations.append(chunks_config)
#
#     DynamicOutputModel = create_dynamic_pydantic_model("DynamicOutputModel", "LLM_BREAKPOINTS", field_configurations)
#     structured_llm = model_enrichment.with_structured_output(DynamicOutputModel, include_raw=True)
#
#     prompt = create_enrichment_prompt(existing_metadata=None, include_chunks=True)
#
#     rt = get_langsmith_run_tree()
#     if rt:
#         rt.metadata["knowledge_id"] = knowledge_id
#         rt.metadata["user_id"] = user_id
#         rt.metadata["chunking_strategy"] = "LLM_BREAKPOINTS"
#         rt.metadata["field_configurations_count"] = len(field_configurations)
#
#     response = invoke_with_retry(structured_llm, [
#         {"role": "system", "content": prompt},
#         {"role": "user", "content": text}
#     ])
#
#     response_dict = serialize(response)
#
#     # Create chunks from the breakpoints
#     chunks = []
#     for chunk in response_dict.get('chunks', []):
#         chunk_text = text[chunk['start_idx']:chunk['end_idx']]
#         chunks.append({"chunk_text": chunk_text})
#
#     response_dict['chunks'] = chunks
#     return response_dict


# # Deprecated strategy
# @traceable(name="Enrich Document")
# def enrich_document_strategy_5(text: str, knowledge_id: str, user_id: Optional[str], object_id: str):
#     """
#     Strategy 5: Uses LangChain's SemanticChunker to split the text.
#     No document-level or chunk-level metadata is generated.
#     """
#     logger.info(f"Executing Strategy 5 for knowledge_id: {knowledge_id}")
#
#     # embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
#     # text_splitter = SemanticChunker(embeddings)
#     # chunks_text = text_splitter.split_text(text)
#
#     response_dict = {}
#     # response_dict["chunks"] = [{"chunk_text": chunk} for chunk in chunks_text]
#     return response_dict
