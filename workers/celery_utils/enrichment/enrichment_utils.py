"""
Enrichment utilities: metadata helpers and usage tracking.

Keep this module free of imports from enrichment_strategies to avoid circular deps.
The dispatcher (enrich_document) lives in dispatcher.py.
"""

import os
import langsmith as ls
import requests
from datetime import date, datetime
from typing import Dict, List, Optional

from services.celery_logger import logger
from celery_utils.db.db_utils import MetadataResponse
from celery_utils.shared.user_usage_tracking import send_enrichment_event
from services import celery_setup_env

# Re-export so callers doing `from enrichment_utils import enrich_document` keep working
from celery_utils.enrichment.dispatcher import enrich_document  # noqa: F401

MODEL_ENRICHMENT = celery_setup_env.ENRICHMENT_MODEL


# --- Metadata helpers ---

def format_metadata_to_string(metadata_response: MetadataResponse) -> str:
    """Format a MetadataResponse into one 'field: values' line per field."""
    if not metadata_response or not metadata_response.fields:
        logger.warning("No metadata response or fields found")
        return ""

    output_lines = []
    for field in metadata_response.fields:
        flat_list = []
        for item in (field.values or []):
            if isinstance(item, list):
                flat_list.extend(item)
            else:
                flat_list.append(item)

        unique_values = sorted({str(v) for v in flat_list if str(v).strip()})
        if unique_values:
            output_lines.append(f"{field.field_name}: {', '.join(unique_values)}")

    return "\n".join(output_lines)


_KNOWLEDGE_API_BASE_URL = os.getenv("KNOWLEDGE_API_BASE_URL", "https://agent.futuresmart.ai/api/knowledge")
_KNOWLEDGE_API_KEY = os.getenv("KNOWLEDGE_API_KEY", "")
_ENRICHMENT_CONTEXT_FETCH_LIMIT = int(os.getenv("ENRICHMENT_CONTEXT_FETCH_LIMIT", 50))


def fetch_existing_field_values(
    knowledge_id: str,
    fields: List[str],
    limit: Optional[int] = None,
) -> Dict[str, List]:
    """
    Fetch previously extracted values for given fields from the knowledge enrichment API.
    Makes one HTTP GET per field. Returns only fields with non-empty values.
    Returns empty dict on any error (non-blocking).

    API response shape (MetadataResponse):
      {"fields": [{"field_name": "...", "values": [...], ...}]}
    """
    fetch_limit = limit or _ENRICHMENT_CONTEXT_FETCH_LIMIT
    headers = {"accept": "application/json", "x-api-key": _KNOWLEDGE_API_KEY}
    result: Dict[str, List] = {}

    for field_name in fields:
        url = (
            f"{_KNOWLEDGE_API_BASE_URL}/v1/{knowledge_id}/enrichment"
            f"?include_metadata=true&field_name={field_name}&limit={fetch_limit}"
        )
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()

            # MetadataResponse: {"fields": [{"field_name": "...", "values": [...]}]}
            metadata_fields = data.get("fields", [])
            if not metadata_fields:
                logger.info(
                    f"[context_fetch] No existing values for field={field_name}, "
                    f"knowledge_id={knowledge_id}"
                )
                continue

            raw_values = metadata_fields[0].get("values", [])
            flat_values = []
            for v in raw_values:
                if isinstance(v, list):
                    flat_values.extend(v)
                elif v is not None:
                    flat_values.append(v)

            unique_values = list({str(v) for v in flat_values if str(v).strip()})
            if unique_values:
                result[field_name] = unique_values
                logger.info(
                    f"[context_fetch] Fetched {len(unique_values)} existing values for "
                    f"field={field_name}, knowledge_id={knowledge_id}"
                )
        except Exception as e:
            logger.warning(
                f"[context_fetch] Failed to fetch field={field_name} for "
                f"knowledge_id={knowledge_id}: {e}"
            )

    return result


def serialize(obj):
    """Recursively serialize Pydantic models to plain dicts.

    Dates are converted to ISO-8601 strings here, and that conversion is the
    ONLY thing standing between a `date` field and a broken payload: Qdrant
    payloads and the SQS message are JSON, `datetime.date` is not
    JSON-serializable, and it has no ``__dict__`` — so without this branch it
    falls through the isinstance checks below and escapes as a live Python
    object. "YYYY-MM-DD" is also exactly what Qdrant's DATETIME payload index
    accepts, so the canonical stored form and the indexable form are the same
    string.
    """
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialize(item) for item in obj]
    if hasattr(obj, "__dict__"):
        return {k: serialize(v) for k, v in obj.__dict__.items()}
    return obj


def get_langsmith_run_tree():
    """Return the current LangSmith run tree, or None if unavailable."""
    try:
        return ls.get_current_run_tree()
    except Exception:
        return None


# --- Usage tracking ---

_STRATEGY_NAMES = {
    1:  "llm_chunking_doc_and_chunk_metadata",
    2:  "deterministic_chunking_doc_and_chunk_metadata",
    6:  "deterministic_chunking_doc_metadata_only",
    7:  "deterministic_chunking_only",
}


def track_enrichment_usage(
    knowledge_id: str,
    object_id: str,
    enrichment_strategy: int,
    token_usage: dict,
    duration_ms: int,
    key_type: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
):
    """Fire a usage-tracking event for an enrichment call."""
    try:
        send_enrichment_event(
            provider=provider or "openai",
            model=model or MODEL_ENRICHMENT or "gpt-4o-mini",
            enrichment_strategy=enrichment_strategy,
            enrichment_strategy_name=_STRATEGY_NAMES.get(enrichment_strategy, str(enrichment_strategy)),
            input_tokens=token_usage.get("input_tokens", 0),
            output_tokens=token_usage.get("output_tokens", 0),
            duration_ms=duration_ms,
            knowledge_id=knowledge_id,
            object_id=object_id,
            key_type=key_type,
        )
    except Exception as e:
        logger.error(f"Error sending enrichment event: {e}")


def track_enrichment_health(
    knowledge_id: str,
    object_id: Optional[str],
    status: str,
    duration_ms: int,
    enrichment_strategy: int,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    exec_meta: Optional[Dict] = None,
    token_usage: Optional[Dict] = None,
    response: Optional[object] = None,
    field_configurations: Optional[List] = None,
    text: Optional[str] = None,
    chunk_count: int = 0,
    owner_id: Optional[str] = None,
    key_type: Optional[str] = None,
    processing_mode=None,
    document_name: Optional[str] = None,
) -> None:
    """Report one enrichment attempt — success or failure — to the analytics
    endpoint. Never raises.

    Companion to :func:`track_enrichment_usage` above, and deliberately a
    separate call rather than an extra argument to it: that function meters (it
    feeds `usage_events` and the monthly token limit) and is only reached after
    a success, whereas this one has to fire on the failure path too. Merging
    them would mean either changing what the limits count, or leaving failures
    unreported — see celery_utils/shared/enrichment_health.py's module docstring.

    `provider`/`model` here are the REQUESTED ones. The actual model that served
    the call is read from `exec_meta`, so a document served by the fallback is
    attributed to the fallback and the divergence stays visible.
    """
    try:
        from celery_utils.shared.enrichment_health import (
            build_field_outcomes,
            send_enrichment_health_event,
        )

        outcomes = build_field_outcomes(response, field_configurations)
        rt = get_langsmith_run_tree()
        send_enrichment_health_event(
            knowledge_id=knowledge_id,
            status=status,
            duration_ms=duration_ms,
            object_id=object_id,
            owner_id=owner_id,
            # char_count is the enrichment input's size, which is what latency
            # and token count actually scale with — so the dashboard can compare
            # models on ms-per-1k-chars instead of on whichever documents each
            # model happened to receive.
            char_count=len(text) if text else 0,
            chunk_count=chunk_count,
            enrichment_strategy=enrichment_strategy,
            enrichment_strategy_name=_STRATEGY_NAMES.get(enrichment_strategy, str(enrichment_strategy)),
            requested_provider=provider,
            requested_model=model,
            exec_meta=exec_meta,
            token_usage=token_usage,
            field_outcomes=outcomes,
            key_type=key_type,
            trace_id=str(getattr(rt, "trace_id", "") or "") or None,
            # rt.id is the RUN id; rt.trace_id above is the trace. Only the run
            # id can be resolved by LangSmith read_run, so this is the field that
            # lets enrichment cost be finalized from what the provider actually
            # billed instead of from a price catalog that goes stale silently.
            langsmith_run_id=str(getattr(rt, "id", "") or "") or None,
            # The mode the attempt actually ran against. Sent even though the
            # document row already stores it: the analytics side groups
            # ATTEMPTS, and a document re-ingested in another mode would
            # otherwise retro-label its own earlier attempts. Accepts either the
            # enum or its wire string -- the worker holds a ProcessingMode here
            # while the API column is TEXT.
            document_processing_mode=(
                getattr(processing_mode, "value", processing_mode)
                if processing_mode is not None else None
            ),
            document_name=document_name,
        )
    except Exception as e:
        logger.error(f"Error sending enrichment health event: {e}")
