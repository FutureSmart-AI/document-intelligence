"""Per-document processing telemetry, measured by the worker (Contract 2).

While one document is processed this module collects what each step took:

* ``parse`` — which reader turned the file into text (``local_text``,
  ``llamaparse`` …), the mode it ran in and how long it took;
* ``extraction`` — every enrichment attempt, grouped by the model that actually
  SERVED it (``exec_meta`` from ``llm_retry.invoke_with_retry``), with the model
  that was requested, whether a fallback ran and why, tokens (incl. cached) and
  time;
* ``embeddings`` — model, tokens and time of the vectors written;
* ``total_ms`` — the whole task.

The record is written to ``document.processing_telemetry`` (migration 056)
when the document completes or fails, via the same DB channel that writes its
status (``db_utils.mark_task_completed`` / ``update_processing_status``). It is
deliberately UNPRICED: the API prices it on read with its one price table
(backend ``utils/processing_telemetry.py``), so the worker never carries a copy
of prices that could drift.

Keyed by ``object_id`` in a process-wide registry rather than a contextvar,
because enrichment can run on worker threads that do not inherit context. A
record for an ``object_id`` that was never started (a crawled child page) rolls
up into the task's root document, so its cost is not lost.

Never raises: telemetry must not fail a document.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.celery_logger import logger

_LOCK = threading.Lock()
_DOCS: Dict[str, Dict[str, Any]] = {}
# The root document of the task running on this thread/process — the fallback
# target for records about object_ids that were never started.
_ROOT = threading.local()
_LAST_ROOT: Dict[str, Optional[str]] = {"object_id": None}


def _norm_provider(provider: Optional[str]) -> str:
    p = (provider or "openai").lower().strip()
    return {"google_genai": "google", "gemini": "google"}.get(p, p)


def start(object_id: str) -> None:
    """Begin a fresh record for ``object_id`` (re-processing replaces the old one)."""
    try:
        with _LOCK:
            _DOCS[object_id] = {"t0": time.time(), "parse": None, "ops": {}}
            _LAST_ROOT["object_id"] = object_id
        _ROOT.object_id = object_id
    except Exception as e:
        logger.debug(f"[document_telemetry] start skipped: {e}")


def _doc(object_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if object_id and object_id in _DOCS:
        return _DOCS[object_id]
    root = getattr(_ROOT, "object_id", None) or _LAST_ROOT.get("object_id")
    return _DOCS.get(root) if root else None


def record_parse(object_id: str, *, parser: Optional[str], mode: Optional[str], ms: int,
                 **extra: Any) -> None:
    """The step that read the document into text. ``extra`` carries whatever
    else the reader reported (pages, credits, auto-shift, fallback reason);
    ``None`` values are dropped."""
    try:
        with _LOCK:
            doc = _doc(object_id)
            if doc is None:
                return
            parse = {"parser": parser or "local_text", "mode": mode, "ms": int(ms or 0)}
            parse.update({k: v for k, v in extra.items() if v is not None and v != ""})
            doc["parse"] = parse
    except Exception as e:
        logger.debug(f"[document_telemetry] parse not recorded: {e}")


def _op(doc: Dict[str, Any], key: tuple, base: Dict[str, Any]) -> Dict[str, Any]:
    op = doc["ops"].get(key)
    if op is None:
        op = {**base, "calls": 0, "failed_calls": 0, "input_tokens": 0,
              "cached_input_tokens": 0, "output_tokens": 0, "latency_ms": 0}
        doc["ops"][key] = op
    return op


def record_extraction(object_id: Optional[str], *, status: str, duration_ms: int,
                      requested_provider: Optional[str], requested_model: Optional[str],
                      exec_meta: Optional[Dict[str, Any]], token_usage: Optional[Dict[str, Any]]) -> None:
    """One enrichment attempt (success or failure), attributed to the model
    that served it."""
    try:
        meta = exec_meta or {}
        usage = token_usage or {}
        provider = _norm_provider(meta.get("provider") or requested_provider)
        model = meta.get("model") or requested_model
        fallback = bool(meta.get("used_fallback"))
        with _LOCK:
            doc = _doc(object_id)
            if doc is None:
                return
            # `model` is the wire model (priced); a registry variant such as
            # "gpt-5.6-luna-low" arrives as configured_model + reasoning_effort, so
            # luna at "low" and luna at "none" stay separate rows.
            configured = meta.get("configured_model")
            op = _op(doc, ("extraction", provider, model,
                           requested_model if fallback else (configured or model),
                           meta.get("reasoning_effort")), {
                "operation": "extraction", "provider": provider, "model": model,
                "requested_provider": _norm_provider(requested_provider) if requested_provider else provider,
                "requested_model": requested_model or configured or model, "fallback": fallback,
            })
            if meta.get("reasoning_effort"):
                op["reasoning_effort"] = str(meta["reasoning_effort"])
            if configured:
                op["configured_model"] = str(configured)
            if fallback and meta.get("fallback_reason"):
                op["fallback_reason"] = str(meta["fallback_reason"])[:300]
            attempts = int(meta.get("attempts") or 1)
            if status == "success":
                op["calls"] += attempts
            else:
                op["failed_calls"] += attempts
                op["last_failure"] = (meta.get("failure_category") or status)
            op["input_tokens"] += int(usage.get("input_tokens") or 0)
            op["cached_input_tokens"] += int(usage.get("cached_input_tokens") or 0)
            op["output_tokens"] += int(usage.get("output_tokens") or 0)
            op["latency_ms"] += int(duration_ms or 0)
    except Exception as e:
        logger.debug(f"[document_telemetry] extraction not recorded: {e}")


def record_embedding(object_id: Optional[str], *, provider: Optional[str], model: Optional[str],
                     input_tokens: int, duration_ms: int) -> None:
    try:
        with _LOCK:
            doc = _doc(object_id)
            if doc is None:
                return
            p = _norm_provider(provider)
            op = _op(doc, ("embeddings", p, model, model), {
                "operation": "embeddings", "provider": p, "model": model,
                "requested_model": model, "fallback": False,
            })
            op["calls"] += 1
            op["input_tokens"] += int(input_tokens or 0)
            op["latency_ms"] += int(duration_ms or 0)
    except Exception as e:
        logger.debug(f"[document_telemetry] embedding not recorded: {e}")


def finish(object_id: str, *, status: str, total_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Close ``object_id``'s record and return it (None if it was never started)."""
    try:
        with _LOCK:
            doc = _DOCS.pop(object_id, None)
            if _LAST_ROOT.get("object_id") == object_id:
                _LAST_ROOT["object_id"] = None
        if getattr(_ROOT, "object_id", None) == object_id:
            _ROOT.object_id = None
        if doc is None:
            return None
        if total_ms is None:
            total_ms = int((time.time() - doc["t0"]) * 1000)
        ops = list(doc["ops"].values())
        ops.sort(key=lambda o: 0 if o["operation"] == "extraction" else 1)
        for o in ops:
            if not o.get("failed_calls"):
                o.pop("failed_calls", None)
        return {
            "version": 1,
            "status": status,
            "total_ms": int(total_ms),
            "parse": doc["parse"],
            "operations": ops,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.debug(f"[document_telemetry] finish skipped: {e}")
        return None
