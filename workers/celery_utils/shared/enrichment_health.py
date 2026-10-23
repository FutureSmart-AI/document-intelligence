"""Enrichment analytics events — one POST per enrichment attempt, success or
failure.

Feeds fs-agent-knowledge's `knowledge_enrichment_events` table, which backs the
enrichment half of the Analytics screen. See that repo's
docs/plan/enrichment-analytics/plan.md for the design and
docs/api/enrichment-health.md for the contract.

**Why this is separate from `user_usage_tracking.send_enrichment_event`.** That
function meters: it feeds `usage_events`, which enforces
`max_llm_tokens_per_month`, and it is only ever called after a successful
enrichment. This one reports outcomes, including the failures — which is the
half a metering table has nowhere to put, and the reason a model comparison
built only on usage data reports zero failures forever and so flatters exactly
the models that fail most. The two are deliberately not merged: changing what
`usage_events` records would change what the limits count.

Both are fire-and-forget. An analytics POST must never fail a document's
indexing, so every function here swallows its own exceptions — a lost event
costs a row on a dashboard, a raised one costs the document.
"""

import os
import uuid
from typing import Any, Dict, List, Optional

import requests

from services.celery_logger import logger

from services.celery_setup_env import (
    KNOWLEDGE_API_BASE_URL as _KNOWLEDGE_API_BASE_URL,
    KNOWLEDGE_API_KEY as _KNOWLEDGE_API_KEY,
)
_TIMEOUT_SECONDS = int(os.getenv("ENRICHMENT_HEALTH_TIMEOUT_SECONDS", 10))

# The same sentinels the strategies' own response validators treat as "not real
# content" (see enrichment_strategies.py::_validate_doc_fields). Coverage has to
# agree with the validator: a field the validator considers empty must not be
# counted as populated, or a document can pass validation at 0% coverage while
# the screen reports it full.
_EMPTY_SENTINELS = {"", "not_found"}


def _is_populated(value: Any) -> bool:
    """Whether one extracted field actually came back with content.

    Mirrors `_validate_doc_fields`'s per-value rules, including the two subtle
    ones: `bool` is checked before `int`/`float` because `bool` is a subclass of
    `int`, and the sentinel test applies only to strings — `item not in {...}`
    against a dict raises TypeError on an unhashable value.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return value not in _EMPTY_SENTINELS
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, (list, tuple, set)):
        return any(
            (item not in _EMPTY_SENTINELS) if isinstance(item, str) else item is not None
            for item in value
        )
    # A nested Pydantic model or any other object is real content by existence.
    return True


def _value_count(value: Any) -> int:
    """How many values a field carries — 1 for a scalar, len() for a list.

    A `List[str]` field filled with one value and one filled with twenty are
    both "populated", and the difference is worth seeing: it is usually the
    difference between a model that lists every skill on a resume and one that
    lists the first.
    """
    if value is None:
        return 0
    if isinstance(value, (list, tuple, set)):
        # Per-item reuse of the same "is this real" rule, so a list of empty
        # strings counts as 0 rather than as its length.
        return sum(1 for v in value if _is_populated(v))
    return 1 if _is_populated(value) else 0


def build_field_outcomes(response: Any, field_configurations: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Per-field coverage for one enrichment response.

    Returns `{"fields_configured", "fields_populated", "field_data"}`, where
    `field_data` is one entry per configured field:
    `{field_name, level, populated, value_count}`.

    The configured schema is read from `field_configurations` when given (those
    rows are the source of truth, and they carry `level`); otherwise it falls
    back to the response model's own fields, which the dynamic schema builder
    generated from exactly those rows. Either way a field the model omitted
    entirely still appears, as `populated: false` — a coverage metric that only
    counted returned fields would always read 100%.
    """
    outcomes: List[Dict[str, Any]] = []

    names_and_levels: List[tuple] = []
    if field_configurations:
        for cfg in field_configurations:
            name = getattr(cfg, "field_name", None)
            if not name:
                continue
            # Skip the structural `chunks` container. Strategy 1 appends it to
            # field_configurations to drive the schema, but it is not an
            # extracted metadata field, and counting it would move every
            # document's coverage by 1/n for a field that is always "populated".
            # The strategies' own response validators skip it for the same
            # reason.
            if getattr(cfg, "field_type", None) == "chunks" or name == "chunks":
                continue
            names_and_levels.append((name, getattr(cfg, "level", None) or "document"))
    elif response is not None:
        model_fields = getattr(response, "model_fields", None) or {}
        names_and_levels = [
            (name, "document") for name in model_fields
            if name not in ("chunks", "chunks_metadata")
        ]

    for name, level in names_and_levels:
        value = getattr(response, name, None) if response is not None else None
        outcomes.append({
            "field_name": name,
            "level": level,
            "populated": _is_populated(value),
            "value_count": _value_count(value),
        })

    return {
        "fields_configured": len(outcomes),
        "fields_populated": sum(1 for o in outcomes if o["populated"]),
        "field_data": outcomes,
    }


def send_enrichment_health_event(
    knowledge_id: str,
    status: str,
    duration_ms: int,
    object_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    document_name: Optional[str] = None,
    char_count: int = 0,
    chunk_count: int = 0,
    enrichment_strategy: Optional[int] = None,
    enrichment_strategy_name: Optional[str] = None,
    requested_provider: Optional[str] = None,
    requested_model: Optional[str] = None,
    exec_meta: Optional[Dict[str, Any]] = None,
    token_usage: Optional[Dict[str, Any]] = None,
    failure_category: Optional[str] = None,
    failure_detail: Optional[str] = None,
    field_outcomes: Optional[Dict[str, Any]] = None,
    key_type: Optional[str] = None,
    trace_id: Optional[str] = None,
    document_processing_mode: Optional[str] = None,
    langsmith_run_id: Optional[str] = None,
) -> None:
    """POST one enrichment attempt to fs-agent-knowledge. Never raises.

    `status` is `success` | `validation_failed` | `error`.

    `exec_meta` is `invoke_with_retry`'s third return value. Read it rather than
    the requested model for provider/model: when the primary model errors and
    the call falls through to the fallback, `exec_meta["model"]` is what actually
    ran, and attributing the document to the requested model would make a
    failing model look like whatever the fallback costs. `requested_*` is sent
    alongside so the divergence stays visible.

    When `failure_category`/`failure_detail` are not passed explicitly they are
    taken from `exec_meta`, which `_exec_meta` populates at each failure return
    site in llm_retry.py.
    """
    try:
        meta = exec_meta or {}
        usage = token_usage or {}
        outcomes = field_outcomes or {}

        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)

        payload = {
            # Generated per attempt: a retried POST of this attempt is a
            # duplicate the API ignores, while a genuine re-run of the whole
            # enrichment gets a new key and is counted as a new attempt.
            "event_key": str(uuid.uuid4()),
            "object_id": object_id,
            "owner_id": owner_id,
            "trace_id": trace_id,
            "document_name": document_name,
            # How the document was READ. Without this the analytics side cannot
            # group by mode at all, and cannot separate "this model scores
            # better" from "this model only ever saw layout_aware input" --
            # get_enrichment_config swaps the model by mode, so the two factors
            # move together unless the knowledge base pins enrichment_model.
            "document_processing_mode": document_processing_mode,
            # The LangSmith RUN id, which is what read_run resolves. Distinct
            # from trace_id above: a trace id is not resolvable against
            # read_run, so sending only that left enrichment cost stuck on the
            # local price-catalog estimate while chat cost got the real figure.
            "langsmith_run_id": langsmith_run_id,
            "char_count": int(char_count or 0),
            "chunk_count": int(chunk_count or 0),
            "status": status,
            "failure_category": failure_category or meta.get("failure_category"),
            "failure_detail": (failure_detail or meta.get("failure_detail") or None),
            "enrichment_strategy": enrichment_strategy,
            "enrichment_strategy_name": enrichment_strategy_name,
            "requested_provider": requested_provider,
            "requested_model": requested_model,
            "provider": meta.get("provider") or requested_provider,
            "model": meta.get("model") or requested_model,
            "used_fallback": bool(meta.get("used_fallback")),
            "fallback_reason": meta.get("fallback_reason"),
            "duration_ms": int(duration_ms or 0),
            # attempts counts the first try too, so retries = attempts - 1.
            "retry_count": max(0, int(meta.get("attempts") or 1) - 1),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": int(usage.get("total_tokens") or (input_tokens + output_tokens)),
            "fields_configured": int(outcomes.get("fields_configured") or 0),
            "fields_populated": int(outcomes.get("fields_populated") or 0),
            "field_data": outcomes.get("field_data") or [],
            "key_type": key_type,
        }

        if not knowledge_id:
            logger.warning("[enrichment-health] no knowledge_id — event dropped")
            return

        url = f"{_KNOWLEDGE_API_BASE_URL}/internal/enrichment-health/events/{knowledge_id}"
        headers = {"accept": "application/json", "x-api-key": _KNOWLEDGE_API_KEY}
        logger.info(
            f"[enrichment-health] POST status={status} model={payload['model']} "
            f"object_id={object_id} duration_ms={payload['duration_ms']} "
            f"coverage={payload['fields_populated']}/{payload['fields_configured']}"
        )
        response = requests.post(url, json=payload, headers=headers, timeout=_TIMEOUT_SECONDS)
        if response.status_code >= 400:
            logger.warning(
                f"[enrichment-health] event rejected: {response.status_code} {response.text[:300]}"
            )
    except Exception as e:
        # Analytics must never break indexing.
        logger.error(f"[enrichment-health] failed to send event: {e}")
