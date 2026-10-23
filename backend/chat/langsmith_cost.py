"""Fetch per-turn cost from LangSmith — the source of truth for $ cost.

LangSmith prices every traced LLM run via its own Model Pricing Map and rolls
the cost up to the root run. Because `run_knowledge_chat_graph` (chat/graph.py)
wraps each turn in a root `KnowledgeChat` run tagged with `chat_id` /
`knowledge_id` metadata, a turn's cost can be pulled either by its own run id
or by that metadata.

**Why this exists.** `chat/pricing.py` prices each turn inline at emit time off
a hardcoded per-1K price map (~23 models). That map goes stale silently — a
model whose published price changes keeps being costed at the old rate forever,
and an unpriced model contributes nothing. This module replaces that estimate
with what LangSmith actually computed, mirroring the donor service
(fs-agent-chat's `chat_utils/langsmith_cost.py`), whose inline pricing was only
ever a placeholder for exactly this reason. See
docs/plan/analytics-and-health/ and docs/modules/analytics-data-sources.md.

**The node_data contract is this repo's, not the donor's.** The donor's
equivalent emits `input_cost`/`output_cost`/`total_cost` per node. This repo's
by_node aggregation (utils/knowledge_health/analytics.py::_node_agg) reads
`cost_usd`, so that is the canonical key here — emitting the donor's shape
would silently null out every by_node cost figure after a finalize. The split
`input_cost`/`output_cost` are kept alongside as extra detail, not as the key
anything aggregates on.

Best-effort and self-contained throughout: any failure returns `{}` / `[]` and
never raises. Cost analytics must never break a chat turn.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _project() -> Optional[str]:
    """The LangSmith project to scope metadata queries to, or None."""
    return (
        os.environ.get("LANGCHAIN_PROJECT")
        or os.environ.get("LANGSMITH_PROJECT")
        or None
    )


def _to_naive(dt: Any) -> Any:
    """LangSmith returns naive timestamps; our windows are tz-aware. Strip the
    tzinfo so a comparison between the two doesn't raise TypeError."""
    return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt


def _cost_of(r) -> Optional[float]:
    """Summed input+output cost for a LangSmith run, or None when unpriced.

    None and 0.0 mean different things and must not be collapsed: LangSmith
    prices runs ASYNCHRONOUSLY, so a run read moments after the turn commonly
    reports tokens with no cost yet. Returning 0.0 there would write a
    confident "$0.00" over a real number — the same "never silently zero" rule
    chat/pricing.py::price_breakdown follows.
    """
    ic = getattr(r, "input_cost", None)
    oc = getattr(r, "output_cost", None)
    if ic is None and oc is None:
        return None
    return float(ic or 0) + float(oc or 0)


def fetch_run_usage(run_id: str) -> Dict[str, Any]:
    """Return `{"cost_usd", "input_tokens", "output_tokens", "provider", "model"}`
    for a SINGLE LangSmith run, looked up directly by run id.

    A direct `read_run` — it needs no `LANGCHAIN_PROJECT` and no metadata
    filter, which is why it is the more robust of the two strategies and the
    one the per-turn finalizer uses.

    Synchronous (the LangSmith SDK client is sync) — call via
    `asyncio.to_thread` from async code. Returns `{}` on any failure or empty
    id. `cost_usd` is None when the run is not yet priced.
    """
    if not run_id:
        return {}
    try:
        from langsmith import Client

        client = Client()  # reads LANGSMITH_API_KEY / LANGCHAIN_ENDPOINT from env
        r = client.read_run(run_id)
        md = (getattr(r, "extra", None) or {}).get("metadata") or {}
        return {
            "cost_usd": _cost_of(r),
            "input_tokens": int(getattr(r, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(r, "output_tokens", 0) or 0),
            "provider": md.get("ls_provider") or md.get("provider"),
            "model": md.get("ls_model_name") or md.get("model"),
        }
    except Exception as e:  # never let a cost fetch break a turn
        logger.warning("[langsmith_cost] read_run failed for %s: %s", run_id, e)
        return {}


def fetch_knowledge_costs(
    knowledge_id: str, since: datetime, until: datetime,
) -> Dict[str, Dict[str, Any]]:
    """Return `{chat_id: {"cost_usd", "input_tokens", "output_tokens"}}` for one
    knowledge base's root `KnowledgeChat` runs in `[since, until)`.

    The metadata strategy: one server-side query instead of a read per row, so
    it is the cheap way to price a wide window. It depends on
    `LANGCHAIN_PROJECT` being set and on the root run carrying both metadata
    keys — turns that ran before that tagging shipped are invisible here and
    need the run-id strategy instead.

    Name match is a PREFIX (`KnowledgeChat%`), not equality: the root run is
    renamed to "KnowledgeChat - <query>" so the LangSmith trace list is
    scannable, and an exact match would miss every renamed run.

    Synchronous — call via `asyncio.to_thread`. Returns `{}` on any failure.
    """
    project = _project()
    if not project:
        logger.warning(
            "[langsmith_cost] no LANGCHAIN_PROJECT/LANGSMITH_PROJECT set — "
            "metadata strategy unavailable, use strategy=run_id"
        )
        return {}
    try:
        from langsmith import Client

        client = Client()
        flt = (
            'and(like(name, "KnowledgeChat%"), '
            f'eq(metadata_key, "knowledge_id"), eq(metadata_value, "{knowledge_id}"))'
        )
        out: Dict[str, Dict[str, Any]] = {}
        until_n = _to_naive(until) if until is not None else None
        for r in client.list_runs(
            project_name=project, is_root=True, filter=flt, start_time=since,
        ):
            st = getattr(r, "start_time", None)
            if until_n is not None and st is not None and _to_naive(st) >= until_n:
                continue
            md = (getattr(r, "extra", None) or {}).get("metadata") or {}
            chat_id = md.get("chat_id")
            if not chat_id:
                continue  # nothing to key the row by
            out[str(chat_id)] = {
                "cost_usd": _cost_of(r),
                "input_tokens": int(getattr(r, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(r, "output_tokens", 0) or 0),
            }
        logger.info(
            "[langsmith_cost] fetched %d run cost(s) for knowledge_id=%s", len(out), knowledge_id
        )
        return out
    except Exception as e:
        logger.error(
            "[langsmith_cost] fetch failed for knowledge_id=%s: %s", knowledge_id, e, exc_info=True
        )
        return {}


# LangSmith run name -> this repo's CANONICAL node label.
#
# The values here are deliberately the names chat/utils/service_calls.py already
# tags locally (refiner / rag_agent / generate_answer / chart), NOT the LangSmith
# run names. node_data is grouped by node_name by both the per-turn view and the
# window-level by_node aggregation, so if a finalize wrote LangSmith's own names
# the same node would split into two series — one set of rows named "rag" and
# another named "rag_agent" — and every by_node comparison across the finalize
# boundary would silently be wrong.
#
# All RAG-internal runs (the retrieval tools, the verifier, the small-KB direct
# path) roll up into `rag_agent`, matching how agent_execution.py tags them;
# answer synthesis stays its own `generate_answer` line.
_NODE_LABELS = {
    # graph nodes (chat/graph.py::build_knowledge_chat_graph)
    "query_refiner": "refiner",
    "rag": "rag_agent",
    "chart": "chart",
    "follow_up": "follow_up",
    # @traceable spans inside the RAG subgraph (chat/rag_node.py, chat/rag_agent/)
    "chunk_search": "rag_agent",
    "doc_details": "rag_agent",
    "retrieval_verifier": "rag_agent",
    "small_kb_direct": "rag_agent",
    "rag_answer_node": "generate_answer",
    # chart generation (chat/chart_generator.py)
    "chart_api": "chart",
}


def _run_latency_ms(r) -> int:
    """Wall-clock duration of a LangSmith run (end - start) in ms; 0 if unknown."""
    st = getattr(r, "start_time", None)
    et = getattr(r, "end_time", None)
    if st is None or et is None:
        return 0
    try:
        return max(0, int((et - st).total_seconds() * 1000))
    except Exception:
        return 0


def fetch_node_data(root_run_id: str) -> list:
    """Per-NODE cost / token / model / latency breakdown for ONE turn, from LangSmith.

    Reads every `llm` run in the turn's trace and attributes each to its nearest
    NAMED node ancestor. The walk up is necessary, not defensive: an llm run's
    direct parent is usually a RunnableSequence, so matching only the immediate
    parent would label almost everything "other".

    Returns entries in THIS REPO's node_data shape — `cost_usd` is the canonical
    cost key (see the module docstring). `[]` on any failure.

    Unlike the donor's version there is no sub-service handling: this service's
    turns never fan out to separately-traced sub-services (`services_used` is
    always `["knowledge"]`), so the whole trace lives under one root run.
    """
    if not root_run_id:
        return []
    project = _project()
    if not project:
        logger.warning("[langsmith_cost] no project set — cannot list trace runs for node_data")
        return []
    try:
        from langsmith import Client

        client = Client()
        root = client.read_run(root_run_id)
        tid = str(getattr(root, "trace_id", None) or root.id)
        runs = list(client.list_runs(project_name=project, trace_id=tid))
        by_id = {str(r.id): r for r in runs}

        def node_of(r) -> str:
            """Nearest named ancestor's canonical label, or 'other'."""
            cur, seen = r, set()
            while cur is not None:
                nm = getattr(cur, "name", None)
                if nm in _NODE_LABELS:
                    return _NODE_LABELS[nm]
                pid = str(getattr(cur, "parent_run_id", None) or "")
                if not pid or pid in seen:  # guard against a cyclic/partial trace
                    break
                seen.add(pid)
                cur = by_id.get(pid)
            return "other"

        agg: Dict[tuple, Dict[str, Any]] = {}
        for r in runs:
            if getattr(r, "run_type", None) != "llm":
                continue
            label = node_of(r)
            md = (getattr(r, "extra", None) or {}).get("metadata") or {}
            model = md.get("ls_model_name") or md.get("model")
            provider = md.get("ls_provider") or md.get("provider")
            # Grouped by (node, model) to match _build_node_data's own grouping —
            # one node can call more than one model in a turn, and the by_node
            # aggregation groups on (node_name, model, provider).
            key = (label, model)
            a = agg.setdefault(key, {
                "node_name": label, "provider": provider, "model": model,
                "calls": 0, "errors": 0, "latency_ms": 0,
                "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                "input_cost": 0.0, "output_cost": 0.0, "cost_usd": 0.0,
                "_cost_known": True,
            })
            a["input_tokens"] += int(getattr(r, "input_tokens", 0) or 0)
            a["output_tokens"] += int(getattr(r, "output_tokens", 0) or 0)
            a["total_tokens"] += int(getattr(r, "total_tokens", 0) or 0)
            a["latency_ms"] += _run_latency_ms(r)
            a["calls"] += 1
            if getattr(r, "error", None):
                a["errors"] += 1
            ic, oc = getattr(r, "input_cost", None), getattr(r, "output_cost", None)
            if ic is None and oc is None:
                # This call is unpriced, so the node's total is a lower bound.
                # Same rule as _build_node_data: report null, not a short sum.
                a["_cost_known"] = False
            else:
                a["input_cost"] += float(ic or 0)
                a["output_cost"] += float(oc or 0)
                a["cost_usd"] += float(ic or 0) + float(oc or 0)
            if not a.get("model"):
                a["model"] = model
            if not a.get("provider"):
                a["provider"] = provider

        out = []
        for a in agg.values():
            cost_known = a.pop("_cost_known", True)
            for k in ("input_cost", "output_cost", "cost_usd"):
                a[k] = round(a[k], 8)
            if not cost_known:
                a["cost_usd"] = None
            a["latency_s"] = round(a["latency_ms"] / 1000, 2)
            out.append(a)
        return out
    except Exception as e:  # never let this break the finalizer
        logger.error(
            "[langsmith_cost] fetch_node_data failed for %s: %s", root_run_id, e, exc_info=True
        )
        return []
