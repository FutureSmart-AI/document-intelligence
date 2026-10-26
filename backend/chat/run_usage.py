"""Run-level usage telemetry for one chat turn: every model call, attributed.

The per-node ``usage_by_model`` / ``service_calls`` records (chat/utils/
service_calls.py) only see the calls a node remembered to report — the
retrieval verifier, the follow-up generator and any later-added call never
reached them, so the API's token totals and the health event's cost were lower
bounds nobody could see the size of. This module counts EVERY chat-model call
made while a turn runs, from a LangChain callback registered for the turn via
``register_configure_hook`` (same mechanism as Database Agent's
``RunUsageHandler``), and turns the count into the ``usage`` block the chat API
returns, streams, persists with the turn and feeds the health event.

Attribution — which operation a call belongs to — is read, in order, from:

1. a ``di_op:<operation>`` tag on the call,
2. the call's run name when it is one of :data:`OPERATIONS`
   (e.g. ``run_name="chart_render"``),
3. the innermost :func:`usage_operation` block the call was made inside (a
   contextvar, so it follows the call into LangGraph/agent sub-tasks),
4. the LangGraph node the call ran in (``langgraph_node`` metadata).

A call none of these claims is listed as ``other_calls`` — an uninstrumented
call can never vanish from the totals.

The provider/model recorded is the one that SERVED the call (the model the
callback saw start), not the configured one. When a call in an operation fails
and the next call in the same operation runs on a different model (LangChain's
``ModelFallbackMiddleware``, the extraction-model fallbacks), that next call is
marked ``fallback: true`` with ``requested_model`` = the model that failed.

Embeddings do not pass through chat callbacks; the code that embeds reports
them with :func:`record_embedding` (tokens counted with the model's tokenizer).

Never raises into a turn: instrumentation problems degrade to a smaller block.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Optional, Tuple

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.context import register_configure_hook

from chat.pricing import PRICE_TABLE_VERSION, estimate_cost
from utils.logger import logger

#: Every operation name a ``usage`` block can contain.
OPERATIONS = (
    "query_refinement",
    "retrieval_agent",
    "retrieval_verifier",
    "answer",
    "chart_spec",
    "chart_render",
    "follow_up",
    "embeddings",
    "other_calls",
)

# LangGraph node -> operation, for calls made directly in a top-level graph
# node without a more specific marker.
_NODE_OPERATIONS = {
    "query_refiner": "query_refinement",
    "chart": "chart_render",
    "follow_up": "follow_up",
}

# LangChain's ls_provider values -> the provider keys chat/pricing.py uses.
_PROVIDER_ALIASES = {
    "google_genai": "google",
    "google_vertexai": "google",
    "google_anthropic_vertex": "anthropic",
    "azure": "openai",
    "azure_openai": "openai",
}

_COUNT_FIELDS = (
    "calls", "failed_calls", "input_tokens", "cached_input_tokens",
    "cache_write_tokens", "output_tokens", "latency_ms",
)


def _norm_provider(provider: Optional[str]) -> str:
    p = (provider or "openai").lower().strip()
    return _PROVIDER_ALIASES.get(p, p)


def _norm_model(model: Optional[str]) -> Optional[str]:
    if not model:
        return None
    m = str(model).strip()
    return m[7:] if m.startswith("models/") else m


# ── operation marker ────────────────────────────────────────────────────────

_CURRENT_OPERATION: ContextVar[Optional[str]] = ContextVar("di_usage_operation", default=None)


@contextmanager
def usage_operation(name: str) -> Iterator[None]:
    """Attribute every model call made inside this block to ``name``.

    Also records the block's wall time as that operation's latency (tool time
    included), so the run log can say how long each stage took even when most
    of it was spent outside a model call.
    """
    token = _CURRENT_OPERATION.set(name)
    start = time.perf_counter()
    try:
        yield
    finally:
        _CURRENT_OPERATION.reset(token)
        collector = _RUN_USAGE.get()
        if collector is not None:
            collector.add_stage_time(name, int((time.perf_counter() - start) * 1000))


def current_operation() -> Optional[str]:
    return _CURRENT_OPERATION.get()


# ── the collector ───────────────────────────────────────────────────────────

class RunUsageHandler(BaseCallbackHandler):
    """Counts every chat-model call made while it is the active collector."""

    # Called inline on the caller's task: the operation contextvar is read at
    # call start, and inline is the only dispatch that guarantees it is the
    # caller's context rather than an executor thread's copy.
    run_inline = True

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._open: Dict[Any, Dict[str, Any]] = {}
        # (operation, provider, model, requested_model) -> counters
        self._rows: Dict[Tuple[str, str, Optional[str], Optional[str]], Dict[str, int]] = {}
        # operation -> (provider, model) of a call that just failed, so the next
        # call in that operation on another model is recognisably a fallback.
        self._failed: Dict[str, Tuple[str, Optional[str]]] = {}
        self._fallback_reason: Dict[Tuple[str, str, Optional[str]], str] = {}
        self.stage_ms: Dict[str, int] = {}

    # LangChain callbacks ---------------------------------------------------
    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None,
                            tags=None, metadata=None, **kwargs) -> None:
        self._start(run_id, tags, metadata, kwargs)

    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None,
                     tags=None, metadata=None, **kwargs) -> None:
        self._start(run_id, tags, metadata, kwargs)

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        try:
            counts = _usage_from_result(response)
            with self._lock:
                call = self._open.pop(run_id, None)
                if call is None:
                    return
                op = call["operation"]
                provider, model = call["provider"], call["model"]
                requested = call.get("requested_model")
                served = counts.pop("served_model", None)
                if not model and served:
                    model = _norm_model(served)
                self._failed.pop(op, None)
                row = self._row(op, provider, model, requested)
                row["calls"] += 1
                row["latency_ms"] += int((time.perf_counter() - call["t0"]) * 1000)
                for f in ("input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens"):
                    row[f] += counts.get(f, 0)
        except Exception as e:  # never break a turn over telemetry
            logger.debug(f"[run_usage] on_llm_end skipped: {e}")

    def on_llm_error(self, error, *, run_id, **kwargs) -> None:
        try:
            with self._lock:
                call = self._open.pop(run_id, None)
                if call is None:
                    return
                op = call["operation"]
                row = self._row(op, call["provider"], call["model"], call.get("requested_model"))
                row["failed_calls"] += 1
                row["latency_ms"] += int((time.perf_counter() - call["t0"]) * 1000)
                self._failed[op] = (call["provider"], call["model"])
                self._last_error = f"{type(error).__name__}: {str(error)[:200]}"
        except Exception as e:
            logger.debug(f"[run_usage] on_llm_error skipped: {e}")

    # Embeddings (reported by the embedding call sites) ---------------------
    def add_embedding(self, provider: Optional[str], model: Optional[str],
                      input_tokens: int, latency_ms: int) -> None:
        with self._lock:
            row = self._row("embeddings", _norm_provider(provider), _norm_model(model), None)
            row["calls"] += 1
            row["input_tokens"] += int(input_tokens or 0)
            row["latency_ms"] += int(latency_ms or 0)

    def add_stage_time(self, operation: str, ms: int) -> None:
        with self._lock:
            self.stage_ms[operation] = self.stage_ms.get(operation, 0) + int(ms)

    # internals -------------------------------------------------------------
    def _start(self, run_id, tags, metadata, kwargs) -> None:
        try:
            meta = metadata or {}
            params = kwargs.get("invocation_params") or {}
            provider = _norm_provider(meta.get("ls_provider"))
            model = _norm_model(meta.get("ls_model_name") or params.get("model") or params.get("model_name"))
            op = _resolve_operation(tags, kwargs.get("name"), meta)
            with self._lock:
                requested = None
                failed = self._failed.get(op)
                if failed and (failed[0], failed[1]) != (provider, model):
                    requested = failed[1]
                    self._fallback_reason[(op, provider, model)] = getattr(self, "_last_error", "") or "primary model call failed"
                self._open[run_id] = {
                    "operation": op, "provider": provider, "model": model,
                    "requested_model": requested, "t0": time.perf_counter(),
                }
        except Exception as e:
            logger.debug(f"[run_usage] call start skipped: {e}")

    def _row(self, op, provider, model, requested) -> Dict[str, int]:
        key = (op, provider, model, requested)
        row = self._rows.get(key)
        if row is None:
            row = dict.fromkeys(_COUNT_FIELDS, 0)
            self._rows[key] = row
        return row

    def snapshot(self) -> List[Dict[str, Any]]:
        """One entry per (operation, provider, model, requested_model)."""
        with self._lock:
            out = []
            for (op, provider, model, requested), v in self._rows.items():
                entry = {"operation": op, "provider": provider, "model": model,
                         "requested_model": requested or model,
                         "fallback": bool(requested and requested != model), **dict(v)}
                reason = self._fallback_reason.get((op, provider, model)) if requested else None
                if reason:
                    entry["fallback_reason"] = reason
                out.append(entry)
            return out


def _resolve_operation(tags: Optional[List[str]], name: Optional[str], meta: Dict[str, Any]) -> str:
    for tag in tags or []:
        if isinstance(tag, str) and tag.startswith("di_op:"):
            op = tag[6:]
            if op in OPERATIONS:
                return op
    if name in OPERATIONS:
        return name  # type: ignore[return-value]
    # A run_name set on a chain (e.g. ``prompt | llm.with_structured_output``)
    # names the parent run, not the chat-model call — but tags and metadata are
    # inherited by the child, so a bare operation tag or ``metadata.operation``
    # set alongside it still reaches the call (chat/local_chart_renderer.py).
    for tag in tags or []:
        if tag in OPERATIONS:
            return tag
    if meta.get("operation") in OPERATIONS:
        return meta["operation"]
    op = _CURRENT_OPERATION.get()
    if op:
        return op
    node = meta.get("langgraph_node")
    if node in _NODE_OPERATIONS:
        return _NODE_OPERATIONS[node]
    return "other_calls"


def _usage_from_result(response: Any) -> Dict[str, Any]:
    """Token counts (+ the served model id) from an LLMResult."""
    counts: Dict[str, Any] = {"input_tokens": 0, "cached_input_tokens": 0,
                              "cache_write_tokens": 0, "output_tokens": 0}
    found = False
    for generations in getattr(response, "generations", None) or []:
        for generation in generations:
            message = getattr(generation, "message", None)
            usage = getattr(message, "usage_metadata", None) or {}
            if usage:
                found = True
                counts["input_tokens"] += int(usage.get("input_tokens") or 0)
                counts["output_tokens"] += int(usage.get("output_tokens") or 0)
                details = usage.get("input_token_details") or {}
                counts["cached_input_tokens"] += int(details.get("cache_read") or 0)
                counts["cache_write_tokens"] += int(details.get("cache_creation") or 0)
            rm = getattr(message, "response_metadata", None) or {}
            if isinstance(rm, dict) and rm.get("model_name") and not counts.get("served_model"):
                counts["served_model"] = rm.get("model_name")
    if not found:
        # Providers that only fill llm_output (older/non-chat paths).
        tu = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
        if isinstance(tu, dict):
            counts["input_tokens"] = int(tu.get("prompt_tokens") or tu.get("input_tokens") or 0)
            counts["output_tokens"] = int(tu.get("completion_tokens") or tu.get("output_tokens") or 0)
            ptd = tu.get("prompt_tokens_details") or {}
            if isinstance(ptd, dict):
                counts["cached_input_tokens"] = int(ptd.get("cached_tokens") or 0)
    return counts


_RUN_USAGE: ContextVar[Optional[RunUsageHandler]] = ContextVar("di_run_usage", default=None)
register_configure_hook(_RUN_USAGE, True)


@contextmanager
def collect_run_usage() -> Iterator[RunUsageHandler]:
    """Count every model call made inside this block, including in tasks it spawns."""
    handler = RunUsageHandler()
    token = _RUN_USAGE.set(handler)
    try:
        yield handler
    finally:
        _RUN_USAGE.reset(token)


def record_embedding(provider: Optional[str], model: Optional[str],
                     input_tokens: int, latency_ms: int) -> None:
    """Report one embedding request to the active turn's collector (no-op outside a turn)."""
    try:
        collector = _RUN_USAGE.get()
        if collector is not None:
            collector.add_embedding(provider, model, input_tokens, latency_ms)
    except Exception as e:
        logger.debug(f"[run_usage] embedding not recorded: {e}")


def count_embedding_tokens(texts: List[str], model: Optional[str] = None) -> int:
    """Tokens an OpenAI embedding request is billed for (cl100k_base, the
    tokenizer of every text-embedding-3 model). 0 when tiktoken is unavailable."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return sum(len(enc.encode(t or "")) for t in texts)
    except Exception:
        return 0


# ── the usage block ─────────────────────────────────────────────────────────

def price_operations(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Price per-operation rows with chat/pricing.py and total them.

    Returns ``{"operations", "estimated_cost_usd", "cost_complete",
    "unpriced_models", "price_table"}``. A row whose model has no price is
    ``estimated_cost_usd: None`` and makes the total incomplete — never ``0``.
    Rows with no tokens (e.g. only failed calls) are not priced and do not make
    the total incomplete.
    """
    operations: List[Dict[str, Any]] = []
    total = 0.0
    priced_any = False
    unpriced: List[str] = []
    for e in entries:
        inp = int(e.get("input_tokens") or 0)
        out = int(e.get("output_tokens") or 0)
        cached = int(e.get("cached_input_tokens") or 0)
        write = int(e.get("cache_write_tokens") or 0)
        cost = None
        if inp or out:
            cost = estimate_cost(e.get("provider"), e.get("model"), inp, out, cached, write)
            if cost is None:
                label = f"{e.get('provider')}/{e.get('model')}"
                if label not in unpriced:
                    unpriced.append(label)
            else:
                total += cost
                priced_any = True
        row = {
            "operation": e.get("operation"),
            "provider": e.get("provider"),
            "model": e.get("model"),
            "requested_model": e.get("requested_model") or e.get("model"),
            "fallback": bool(e.get("fallback")),
            "calls": int(e.get("calls") or 0),
            "input_tokens": inp,
            "cached_input_tokens": cached,
            "output_tokens": out,
            "latency_ms": int(e.get("latency_ms") or 0),
            "estimated_cost_usd": cost,
        }
        if write:
            row["cache_write_tokens"] = write
        if e.get("failed_calls"):
            row["failed_calls"] = int(e["failed_calls"])
        if e.get("fallback_reason"):
            row["fallback_reason"] = e["fallback_reason"]
        operations.append(row)
    return {
        "operations": operations,
        # None when nothing could be priced: an all-unpriced turn is unknown, not free.
        "estimated_cost_usd": round(total, 9) if (priced_any or not unpriced) else None,
        "cost_complete": not unpriced,
        "unpriced_models": unpriced,
        "price_table": PRICE_TABLE_VERSION,
    }


def _op_order(row: Dict[str, Any]) -> int:
    try:
        return OPERATIONS.index(row.get("operation"))
    except ValueError:
        return len(OPERATIONS)


def build_usage_block(handler: Optional[RunUsageHandler], latency_ms: Optional[int],
                      service_calls: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The turn's ``usage`` block (Contract 1). Never raises.

    Top-level token totals cover chat-model calls (every operation except
    ``embeddings``, whose tokens are input-only and listed on their own row);
    ``estimated_cost_usd`` covers every priced row including embeddings.
    ``service_calls`` is passed through unchanged for existing consumers.
    """
    service_calls = list(service_calls or [])
    try:
        entries = handler.snapshot() if handler is not None else []
        priced = price_operations(sorted(entries, key=_op_order))
        llm_rows = [r for r in priced["operations"] if r["operation"] != "embeddings"]
        input_tokens = sum(r["input_tokens"] for r in llm_rows)
        output_tokens = sum(r["output_tokens"] for r in llm_rows)
        block: Dict[str, Any] = {
            "latency_ms": int(latency_ms) if latency_ms is not None else None,
            "input_tokens": input_tokens,
            "cached_input_tokens": sum(r["cached_input_tokens"] for r in llm_rows),
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "calls": sum(r["calls"] for r in llm_rows),
            **priced,
            "operation_latency_ms": dict(handler.stage_ms) if handler is not None else {},
            "service_calls": service_calls,
        }
        if not priced["operations"]:
            block["estimated_cost_usd"] = None
        return block
    except Exception as e:
        logger.warning(f"[run_usage] usage block degraded: {e}")
        inp = sum(int(c.get("input_tokens") or 0) for c in service_calls)
        out = sum(int(c.get("output_tokens") or 0) for c in service_calls)
        return {"latency_ms": latency_ms, "input_tokens": inp, "output_tokens": out,
                "total_tokens": inp + out, "service_calls": service_calls}


def cost_breakdown_by_model(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collapse a usage block's operations to one line per (provider, model) —
    the shape knowledge_health_events.cost_breakdown has always stored."""
    lines: Dict[Tuple[Any, Any], Dict[str, Any]] = {}
    for r in block.get("operations") or []:
        key = (r.get("provider"), r.get("model"))
        line = lines.setdefault(key, {"provider": key[0], "model": key[1], "input_tokens": 0,
                                      "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0,
                                      "_priced": True})
        line["input_tokens"] += int(r.get("input_tokens") or 0)
        line["output_tokens"] += int(r.get("output_tokens") or 0)
        line["total_tokens"] = line["input_tokens"] + line["output_tokens"]
        if r.get("cached_input_tokens"):
            line["cached_input_tokens"] = line.get("cached_input_tokens", 0) + int(r["cached_input_tokens"])
        c = r.get("estimated_cost_usd")
        if c is None:
            if r.get("input_tokens") or r.get("output_tokens"):
                line["_priced"] = False
        else:
            line["cost_usd"] += float(c)
    out = []
    for line in lines.values():
        priced = line.pop("_priced")
        line["cost_usd"] = round(line["cost_usd"], 6) if priced else None
        out.append(line)
    return out


async def in_operation(name: str, awaitable: Any) -> Any:
    """``await in_operation("answer", generate_rag_answer(...))`` — run one
    awaitable with its model calls attributed to ``name``. The coroutine has not
    started when it is passed in, so every call it makes sees the marker."""
    with usage_operation(name):
        return await awaitable
