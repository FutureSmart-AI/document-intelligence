# Sync Usage Tracking Utilities
# This file will contain sync functions for tracking user usage (for Celery workers)
# Moved from workers/celery_utils/usage_tracking_utils.py

"""
Sync Usage Tracking Utils for Worker Tasks

This module contains synchronous utility functions for tracking usage limits
across different file types (text, files, weblinks) and updating usage metrics in the database.
Only includes functions that are actually used in worker tasks.

Events are posted to fs-agent-knowledge's in-process usage-events endpoint, not the
old standalone fs-agent-usage-limits service (USAGE_TRACKING_BASE_URL) — that
service's event-recording logic now lives inside fs-agent-knowledge (see its
docs/plan/usage-and-limits/). Same KNOWLEDGE_API_BASE_URL/KNOWLEDGE_API_KEY
celery_utils/enrichment/enrichment_utils.py already uses for enrichment context.
"""

import os
from typing import Optional
from datetime import datetime
from services.celery_logger import logger
import requests

from celery_utils.db.db_utils import get_knowledge_config_sync

from services.celery_setup_env import (
    KNOWLEDGE_API_BASE_URL as _KNOWLEDGE_API_BASE_URL,
    KNOWLEDGE_API_KEY as _KNOWLEDGE_API_KEY,
)


def get_owner_id_from_service_id(service_id: Optional[str]) -> Optional[str]:
    """
    Get owner_id from service_id by querying the knowledge_config table.
    
    Args:
        service_id (Optional[str]): The service_id (knowledge_id) to look up
        
    Returns:
        Optional[str]: The owner_id if found, None otherwise
    """
    if not service_id:
        return None
    
    try:
        knowledge_config = get_knowledge_config_sync(service_id)
        if knowledge_config:
            return knowledge_config.owner_id
        return None
    except Exception as e:
        logger.error(f"Error getting owner_id from service_id {service_id}: {e}")
        return None


def post_processing_event(payload: dict) -> Optional[dict]:
    """Helper function to send processing events to fs-agent-knowledge's usage
    events endpoint.

    The endpoint is **owner-scoped** (`/v1/owners/{owner_id}/usage/events`) — usage is
    recorded per account, across all of an owner's knowledge bases. `owner_id` is taken
    from the events themselves (every `send_*_event()` in this module resolves it via
    `get_owner_id_from_service_id`); an event we can't attribute to an owner can't be
    recorded, so it's logged and dropped rather than posted to a malformed URL.

    The wire field for the knowledge base is `knowledge_id`, not this module's internal
    `service_id`, so events are translated here — one place — instead of changing all
    six builders' payload shape.
    """
    try:
        events = payload.get("events") or []
        owner_id = next((e.get("owner_id") for e in events if e.get("owner_id")), None)
        if not owner_id:
            logger.warning(
                "post_processing_event: no owner_id resolvable on any event, cannot record "
                f"(service_id={events[0].get('service_id') if events else None})"
            )
            return None

        # service_id -> knowledge_id; owner_id comes from the path, not the body.
        translated = []
        for event in events:
            item = {k: v for k, v in event.items() if k not in ("service_id", "owner_id")}
            if event.get("service_id"):
                item["knowledge_id"] = event["service_id"]
            translated.append(item)
        body = {"events": translated}

        HEADERS = {
            "accept": "application/json",
            "x-api-key": _KNOWLEDGE_API_KEY,
            "Content-Type": "application/json"
        }
        events_endpoint = f"{_KNOWLEDGE_API_BASE_URL}/v1/owners/{owner_id}/usage/events"

        logger.info(f"payload: {body}")
        response = requests.post(events_endpoint, headers=HEADERS, json=body, timeout=10)
        response.raise_for_status()
        logger.info(f"Processing event sent successfully. Status: {response.status_code}")
        if response.headers.get('content-type', '').startswith('application/json'):
            return response.json()
        return {"status": "success", "status_code": response.status_code}
    except Exception as e:
        logger.error(f"Unexpected error sending processing event to API: {e}")
        return None


def send_file_processing_event(
    file_extension: str,
    extraction_method: str,
    char_count: int,
    llama_parse_page_count: int,
    audio_seconds: Optional[float],
    duration_ms: int,
    knowledge_id: Optional[str] = None,
    object_id: Optional[str] = None,
    key_type: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    requested_mode: Optional[str] = None,
    effective_mode: Optional[str] = None,
    parse_mode: Optional[str] = None,
    llama_parse_credits: int = 0,
    mode_auto_shifted_from: Optional[str] = None,
):
    """
    Send file processing event to the usage events API.

    Args:
        file_extension (str): File extension without dot (e.g., "pdf", "docx", "mp3")
        extraction_method (str): Extraction method ("llama_parse", "local", "whisper_openai", "whisper_groq")
        char_count (int): Total characters extracted
        llama_parse_page_count (int): Pages processed via LlamaParse (0 if not used)
        audio_seconds (Optional[float]): Audio duration in seconds (None or 0 if not audio)
        duration_ms (int): Time taken to process in milliseconds
        knowledge_id (Optional[str]): Knowledge ID (service_id)
        object_id (Optional[str]): Object ID (request_id)
        provider (Optional[str]): Transcript provider (e.g., "openai", "groq") for audio files
        model (Optional[str]): Transcript model (e.g., "whisper-1", "whisper-large-v3-turbo") for audio files
        temperature (Optional[float]): Transcript temperature for audio files
        requested_mode (Optional[str]): Processing mode asked for ("scanned", "visual", ...)
        effective_mode (Optional[str]): Processing mode that actually ran — differs from
            requested_mode when a mode does not apply to the file type, or when an
            image-only PDF was auto-shifted to OCR
        parse_mode (Optional[str]): The billing key this parse ran under — a literal
            LlamaParse parse mode ("parse_page_with_llm", ...) or a "tier:<name>" key
            for a tier-based mode (see processing_modes._BILLING_KEY)
        llama_parse_credits (int): Estimated LlamaParse credits for this parse. An
            estimate from published rates, not the billed figure — see
            processing_modes._CREDITS_PER_PAGE
        mode_auto_shifted_from (Optional[str]): Set when we changed the mode ourselves,
            holding the mode originally requested
    """
    try:
        # Normalize file extension (remove leading dot, lowercase)
        file_ext = file_extension.lstrip('.').lower() if file_extension else ''

        # Build metadata
        metadata = {
            "file_extension": file_ext,
            "extraction_method": extraction_method
        }
        if provider is not None:
            metadata["provider"] = provider
        if model is not None:
            metadata["model"] = model
        if temperature is not None:
            metadata["temperature"] = temperature
        # Mode detail rides in metadata rather than becoming new columns: metadata is a
        # JSON field, so adding keys here needs no database migration, and these are
        # dimensions to slice a report by rather than numbers to sum.
        if requested_mode is not None:
            metadata["requested_mode"] = requested_mode
        if effective_mode is not None:
            metadata["effective_mode"] = effective_mode
        if parse_mode is not None:
            metadata["parse_mode"] = parse_mode
        if mode_auto_shifted_from is not None:
            metadata["mode_auto_shifted_from"] = mode_auto_shifted_from

        # Build event
        event = {
            "service_id": knowledge_id,
            "owner_id": get_owner_id_from_service_id(knowledge_id),
            "request_id": object_id,
            "key_type": key_type,
            "service_type": "knowledge",
            "operation": "data_ingestion",
            "label": "file_processing",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "metadata": metadata,
            "metrics": [
                {"name": "duration_ms", "value": duration_ms},
                {"name": "file_count", "value": 1},
                {"name": "char_count", "value": char_count},
                {"name": "llama_parse_page_count", "value": llama_parse_page_count},
                # Pages and credits are both recorded because they answer different
                # questions: pages is how much was processed, credits is what it cost.
                # The same page count can differ tenfold in cost depending on mode.
                {"name": "llama_parse_credits", "value": llama_parse_credits},
                {"name": "audio_seconds", "value": audio_seconds if audio_seconds is not None else 0.0}
            ]
        }

        # Send event to API
        payload = {"events": [event]}
        logger.info(f"Sending file processing event: service_id={knowledge_id}, file_extension={file_ext}, extraction_method={extraction_method}")
        post_processing_event(payload)

    except Exception as e:
        logger.error(f"Error sending file processing event: {e}")



def send_weblink_processing_event(
    scrape_method: str,
    crawl_depth: int,
    processed_page_count: int,
    char_count: int,
    duration_ms: int,
    knowledge_id: Optional[str] = None,
    object_id: Optional[str] = None,
    key_type: Optional[str] = None,
    requested_mode: Optional[str] = None,
    effective_mode: Optional[str] = None,
    parse_mode: Optional[str] = None,
    llama_parse_credits: int = 0
):
    """
    Send weblink processing event to the usage events API.

    Args:
        scrape_method (str): Scraping method ("firecrawl_scrape", "firecrawl_crawl",
            "langchain_scrape", "newspaper_scrape", or "file_<parse_mode>" when the URL
            resolved to a file and no scraper ran)
        crawl_depth (int): Maximum crawl depth (0 for scrape)
        processed_page_count (int): Number of pages/URLs processed
        char_count (int): Total characters extracted
        duration_ms (int): Time taken to process in milliseconds
        knowledge_id (Optional[str]): Knowledge ID (service_id)
        object_id (Optional[str]): Object ID (request_id)
        requested_mode (Optional[str]): The processing mode the caller asked for
        effective_mode (Optional[str]): The mode that actually ran, post auto-shift.
            None for an HTML page, which has no mode — the scraper is the mechanism.
        parse_mode (Optional[str]): LlamaParse billing key, when LlamaParse read a file
            behind the URL
        llama_parse_credits (int): Estimated LlamaParse credits. A URL pointing at a
            scanned PDF costs the same as the identical uploaded file; before this the
            weblink path reported none of it, so that spend was invisible in analytics.
    """
    try:
        # Build metadata
        metadata = {
            "scrape_method": scrape_method,
            "crawl_depth": crawl_depth,
            "requested_mode": requested_mode,
            "effective_mode": effective_mode,
            "parse_mode": parse_mode
        }

        # Build event
        event = {
            "service_id": knowledge_id,
            "owner_id": get_owner_id_from_service_id(knowledge_id),
            "request_id": object_id,
            "key_type": key_type,
            "service_type": "knowledge",
            "operation": "data_ingestion",
            "label": "weblink_processing",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "metadata": metadata,
            "metrics": [
                {"name": "duration_ms", "value": duration_ms},
                {"name": "processed_page_count", "value": processed_page_count},
                {"name": "char_count", "value": char_count},
                {"name": "llama_parse_credits", "value": llama_parse_credits}
            ]
        }

        # Send event to API
        payload = {"events": [event]}
        logger.info(
            f"Sending weblink processing event: service_id={knowledge_id}, scrape_method={scrape_method}, "
            f"processed_page_count={processed_page_count}, llama_parse_credits={llama_parse_credits}"
        )
        post_processing_event(payload)

    except Exception as e:
        logger.error(f"Error sending weblink processing event: {e}")


def send_text_processing_event(
    char_count: int,
    duration_ms: int,
    knowledge_id: Optional[str] = None,
    object_id: Optional[str] = None,
    key_type: Optional[str] = None
):
    """
    Send text processing event to the usage events API.

    Args:
        char_count (int): Total characters in text
        duration_ms (int): Time taken to process in milliseconds
        knowledge_id (Optional[str]): Knowledge ID (service_id)
        object_id (Optional[str]): Object ID (request_id)
    """
    try:
        # Build event
        event = {
            "service_id": knowledge_id,
            "owner_id": get_owner_id_from_service_id(knowledge_id),
            "request_id": object_id,
            "key_type": key_type,
            "service_type": "knowledge",
            "operation": "data_ingestion",
            "label": "raw_text_processing",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "metadata": {},
            "metrics": [
                {"name": "duration_ms", "value": duration_ms},
                {"name": "text_count", "value": 1},
                {"name": "char_count", "value": char_count}
            ]
        }

        # Send event to API
        payload = {"events": [event]}
        logger.info(f"Sending text processing event: service_id={knowledge_id}, char_count={char_count}")
        post_processing_event(payload)

    except Exception as e:
        logger.error(f"Error sending text processing event: {e}")


def send_enrichment_event(
    provider: str,
    model: str,
    enrichment_strategy: int,
    enrichment_strategy_name: str,
    input_tokens: int,
    output_tokens: int,
    duration_ms: int,
    knowledge_id: Optional[str] = None,
    object_id: Optional[str] = None,
    key_type: Optional[str] = None
):
    """
    Send document enrichment event to the usage events API.

    Args:
        provider (str): LLM provider (e.g., "openai")
        model (str): Model name (e.g., "gpt-4o-mini", "gpt-5-mini")
        enrichment_strategy (int): Enrichment strategy number (e.g., 1, 6, 7)
        enrichment_strategy_name (str): Human-readable strategy identifier
        input_tokens (int): Tokens sent to LLM
        output_tokens (int): Tokens received from LLM
        duration_ms (int): Time taken to process in milliseconds
        knowledge_id (Optional[str]): Knowledge ID (service_id)
        object_id (Optional[str]): Object ID (request_id)
    """
    try:
        # Build metadata
        metadata = {
            "provider": provider,
            "model": model,
            "enrichment_strategy": enrichment_strategy,
            "enrichment_strategy_name": enrichment_strategy_name,
        }

        # Build event
        total_tokens = input_tokens + output_tokens
        event = {
            "service_id": knowledge_id,
            "owner_id": get_owner_id_from_service_id(knowledge_id),
            "request_id": object_id,
            "key_type": key_type,
            "service_type": "knowledge",
            "operation": "llm_call",
            "label": "document_enrichment",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "metadata": metadata,
            "metrics": [
                {"name": "duration_ms", "value": duration_ms},
                {"name": "input_tokens", "value": input_tokens},
                {"name": "output_tokens", "value": output_tokens},
                {"name": "total_tokens", "value": total_tokens}
            ]
        }

        # Send event to API
        payload = {"events": [event]}
        logger.info(f"Sending enrichment event: service_id={knowledge_id}, provider={provider}, model={model}, enrichment_strategy={enrichment_strategy}, enrichment_strategy_name={enrichment_strategy_name}")
        post_processing_event(payload)

    except Exception as e:
        logger.error(f"Error sending enrichment event: {e}")


def send_embedding_event(
    provider: str,
    model: str,
    embedding_tokens: int,
    char_count: int,
    duration_ms: int,
    knowledge_id: Optional[str] = None,
    object_id: Optional[str] = None,
    key_type: Optional[str] = None
):
    """
    Send embedding generation event to the usage events API.

    Args:
        provider (str): Embedding provider ("openai" or "huggingface")
        model (str): Model name (e.g., "text-embedding-3-large", "paraphrase-multilingual-mpnet-base-v2")
        embedding_tokens (int): Total tokens used for embeddings (0 for HuggingFace)
        char_count (int): Total characters that were embedded
        duration_ms (int): Time taken to process in milliseconds
        knowledge_id (Optional[str]): Knowledge ID (service_id)
        object_id (Optional[str]): Object ID (request_id)
    """
    try:
        # Build event
        event = {
            "service_id": knowledge_id,
            "owner_id": get_owner_id_from_service_id(knowledge_id),
            "request_id": object_id,
            "key_type": key_type,
            "service_type": "knowledge",
            "operation": "embedding_generation",
            "label": "content_embedding",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "metadata": {
                "provider": provider,
                "model": model
            },
            "metrics": [
                {"name": "duration_ms", "value": duration_ms},
                {"name": "input_tokens", "value": embedding_tokens},
                {"name": "char_count", "value": char_count}
            ]
        }

        # Send event to API
        payload = {"events": [event]}
        logger.info(f"Sending embedding event: service_id={knowledge_id}, provider={provider}, model={model}, embedding_tokens={embedding_tokens}")
        post_processing_event(payload)

    except Exception as e:
        logger.error(f"Error sending embedding event: {e}")


def send_vector_indexing_event(
    provider: str,
    model: str,
    chunks_only: bool,
    document_vector_count: int,
    chunk_vector_count: int,
    payload_size_bytes: int,
    duration_ms: int,
    knowledge_id: Optional[str] = None,
    object_id: Optional[str] = None,
    key_type: Optional[str] = None
):
    """
    Send vector indexing event to the usage events API.

    Args:
        provider (str): Embedding provider ("openai" or "huggingface")
        model (str): Model name (e.g., "text-embedding-3-large", "paraphrase-multilingual-mpnet-base-v2")
        chunks_only (bool): Whether only chunks were indexed (True) or documents too (False)
        document_vector_count (int): Number of document vectors indexed
        chunk_vector_count (int): Number of chunk vectors indexed
        payload_size_bytes (int): Size of indexed payload in bytes
        duration_ms (int): Time taken to process in milliseconds
        knowledge_id (Optional[str]): Knowledge ID (service_id)
        object_id (Optional[str]): Object ID (request_id)
    """
    try:
        # Build event
        event = {
            "service_id": knowledge_id,
            "owner_id": get_owner_id_from_service_id(knowledge_id),
            "request_id": object_id,
            "key_type": key_type,
            "service_type": "knowledge",
            "operation": "vector_db_write",
            "label": "vector_indexing",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "metadata": {
                "provider": provider,
                "model": model,
                "chunks_only": chunks_only
            },
            "metrics": [
                {"name": "duration_ms", "value": duration_ms},
                {"name": "document_vector_count", "value": document_vector_count},
                {"name": "chunk_vector_count", "value": chunk_vector_count},
                {"name": "payload_size_bytes", "value": payload_size_bytes}
            ]
        }

        # Send event to API
        payload = {"events": [event]}
        logger.info(f"Sending vector indexing event: service_id={knowledge_id}, provider={provider}, model={model}, document_vector_count={document_vector_count}, chunk_vector_count={chunk_vector_count}")
        post_processing_event(payload)

    except Exception as e:
        logger.error(f"Error sending vector indexing event: {e}")


