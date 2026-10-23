"""Build per-service-CALL breakdown records for the Agent Health query drill-down.

Each node (router / refiner / rephrase / rag / integration / nl2sql /
connected_agent / custom_action) times its own work at OUR end and appends one
record via :func:`build_service_call`. The records ride on the ``service_calls``
state accumulator (``operator.add``) exactly like ``usage_by_model`` — so parallel
fan-out (e.g. 3 integrations) yields 3 records — and join_results hoists the ones
produced inside tool runners onto the top level.

Cost per call: in-graph calls are priced locally via ``pricing.estimate_cost``
(accurate once the model is in the price table; ``None`` when unknown). Sub-service
calls carry their LangSmith ``trace_id`` + ``cost_source`` so the per-query cost
finalizer can back-fill the EXACT LangSmith cost later (LangSmith prices async).
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Dict, Optional


@contextmanager
def timed():
    """Wall-clock timer (our end). Usage::

        with timed() as t:
            ... work ...
        latency_ms = t()          # int milliseconds
    """
    start = time.perf_counter()
    done: Dict[str, float] = {}

    def _elapsed() -> int:
        return int((done.get("end", time.perf_counter()) - start) * 1000)

    try:
        yield _elapsed
    finally:
        done["end"] = time.perf_counter()


def build_service_call(
    *,
    service: str,
    latency_ms: int,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    id: Optional[str] = None,
    trace_id: Optional[str] = None,
    cost_usd: Optional[float] = None,
    cost_source: Optional[str] = None,
) -> Dict[str, Any]:
    """One ``service_calls`` record. ``cost_usd`` is used verbatim when given
    (e.g. a sub-service's LangSmith cost); otherwise it's priced locally from the
    tokens (``None`` when the model is unknown). Never raises."""
    inp = int(input_tokens or 0)
    out = int(output_tokens or 0)
    cost = cost_usd
    if cost is None:
        try:
            from chat.pricing import estimate_cost
            cost = estimate_cost(provider, model, inp, out)
        except Exception:
            cost = None
    rec: Dict[str, Any] = {
        "service": service,
        "latency_ms": int(latency_ms or 0),
        "provider": provider,
        "model": model,
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": inp + out,
        "cost_usd": cost,
    }
    if id:
        rec["id"] = id
    if trace_id:
        rec["trace_id"] = trace_id
    if cost_source:
        rec["cost_source"] = cost_source
    return rec
